import logging
import os
import re
import signal
import sys
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

try:
    from waitress import serve
except ImportError:
    serve = None


HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]+$")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DEFAULT_THREADS = 4
MAX_THREADS = 64
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
SERVER_LOCK_NAME = "ruc_server.lock"


class RuntimeConfigError(RuntimeError):
    pass


def parse_int_setting(name, default, minimum, maximum):

    raw_value = os.environ.get(name, str(default)).strip()

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeConfigError(f"{name} must be an integer.") from exc

    if value < minimum or value > maximum:
        raise RuntimeConfigError(f"{name} must be between {minimum} and {maximum}.")

    return value


def parse_host():

    host = os.environ.get("RUC_HOST", DEFAULT_HOST).strip()

    if not host:
        raise RuntimeConfigError("RUC_HOST cannot be blank.")

    if len(host) > 253 or not HOST_PATTERN.match(host) or ".." in host:
        raise RuntimeConfigError("RUC_HOST is not a valid bind host.")

    return host


def parse_port():

    return parse_int_setting("RUC_PORT", DEFAULT_PORT, 1, 65535)


def parse_threads():

    return parse_int_setting("RUC_THREADS", DEFAULT_THREADS, 1, MAX_THREADS)


def parse_log_level():

    raw_level = os.environ.get("RUC_LOG_LEVEL", "INFO").strip().upper() or "INFO"
    level = getattr(logging, raw_level, None)

    if not isinstance(level, int):
        raise RuntimeConfigError("RUC_LOG_LEVEL must be a valid Python logging level.")

    return raw_level, level


def resolve_log_dir(base_dir):

    raw_dir = os.environ.get("RUC_LOG_DIR", "logs").strip() or "logs"
    path = raw_dir if os.path.isabs(raw_dir) else os.path.join(base_dir, raw_dir)
    path = os.path.abspath(path)
    normalized_base_dir = os.path.normcase(os.path.abspath(base_dir))
    normalized_path = os.path.normcase(path)

    if normalized_path != normalized_base_dir and not normalized_path.startswith(
        normalized_base_dir + os.sep
    ):
        raise RuntimeConfigError("RUC_LOG_DIR must be inside the RUC project folder.")

    os.makedirs(path, exist_ok=True)
    return path


def configure_logging(base_dir):

    level_name, level = parse_log_level()
    log_dir = resolve_log_dir(base_dir)
    log_path = os.path.join(log_dir, "ruc.log")

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return logging.getLogger("ruc.production"), log_path, level_name


def read_lock_metadata(lock_path):

    metadata = {}

    try:
        with open(lock_path, "r", encoding="utf-8") as lock_file:
            for line in lock_file.read(2048).splitlines():
                if "=" not in line:
                    continue

                key, value = line.split("=", 1)
                metadata[key.strip()] = value.strip()
    except OSError:
        pass

    return metadata


def server_lock_is_stale(lock_path, ruc_app):

    metadata = read_lock_metadata(lock_path)
    process_state = ruc_app.process_is_running(metadata.get("pid"))

    if process_state is False:
        return True

    try:
        lock_age = time.time() - os.path.getmtime(lock_path)
    except OSError:
        return False

    return process_state is None and lock_age > ruc_app.WORKBOOK_STALE_LOCK_SECONDS


def remove_stale_server_lock(lock_path, ruc_app):

    if not os.path.exists(lock_path):
        return True

    if not server_lock_is_stale(lock_path, ruc_app):
        return False

    try:
        os.remove(lock_path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


@contextmanager
def production_server_lock(ruc_app):

    os.makedirs(ruc_app.RUNTIME_DIR, exist_ok=True)
    lock_path = os.path.join(ruc_app.RUNTIME_DIR, SERVER_LOCK_NAME)
    lock_fd = None

    try:
        while True:
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                metadata = (
                    f"pid={os.getpid()}\n"
                    f"created_at={time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
                    "purpose=production_server\n"
                )
                os.write(lock_fd, metadata.encode("utf-8"))
                break
            except FileExistsError as exc:
                if remove_stale_server_lock(lock_path, ruc_app):
                    continue

                raise RuntimeConfigError(
                    "Another RUC production server instance appears to be running."
                ) from exc

        yield lock_path
    finally:
        if lock_fd is not None:
            os.close(lock_fd)

        try:
            if os.path.exists(lock_path):
                metadata = read_lock_metadata(lock_path)

                if metadata.get("pid") == str(os.getpid()):
                    os.remove(lock_path)
        except OSError:
            pass


def raise_keyboard_interrupt(signum, frame):

    raise KeyboardInterrupt


def configure_shutdown_signals():

    for signal_name in ("SIGBREAK", "SIGTERM"):
        shutdown_signal = getattr(signal, signal_name, None)

        if shutdown_signal is not None:
            try:
                signal.signal(shutdown_signal, raise_keyboard_interrupt)
            except (OSError, RuntimeError, ValueError):
                pass


def main():

    if serve is None:
        sys.stderr.write(
            "RUC production startup failed: waitress is not installed in the local virtual environment.\n"
        )
        return 1

    try:
        import app as ruc_app
    except Exception as exc:
        sys.stderr.write(
            "RUC production startup failed: application configuration could not be loaded.\n"
        )
        sys.stderr.write(f"{exc.__class__.__name__}: {exc}\n")
        return 1

    try:
        host = parse_host()
        port = parse_port()
        threads = parse_threads()
        logger, log_path, level_name = configure_logging(ruc_app.BASE_DIR)
    except RuntimeConfigError as exc:
        sys.stderr.write(f"RUC production startup failed: {exc}\n")
        return 1
    except OSError as exc:
        sys.stderr.write("RUC production startup failed: technical log file could not be opened.\n")
        sys.stderr.write(f"{exc.__class__.__name__}: {exc}\n")
        return 1

    logger.info(
        "RUC production server starting host=%s port=%s threads=%s log_level=%s",
        host,
        port,
        threads,
        level_name,
    )
    logger.info("RUC technical log active at %s", os.path.basename(log_path))
    configure_shutdown_signals()

    try:
        with production_server_lock(ruc_app):
            serve(
                ruc_app.app,
                host=host,
                port=port,
                threads=threads,
                ident="RUC System",
            )
    except KeyboardInterrupt:
        logger.info("RUC production server stopped by operator.")
        return 0
    except RuntimeConfigError as exc:
        logger.error("RUC production startup failed: %s", exc)
        sys.stderr.write(f"RUC production startup failed: {exc}\n")
        return 1
    except Exception as exc:
        logger.exception("RUC production server stopped unexpectedly.")
        sys.stderr.write(f"RUC production server failed: {exc.__class__.__name__}: {exc}\n")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
