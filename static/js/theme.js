;(function () {
  var THEME_KEY = 'ruc-theme'
  var allowedThemes = ['system', 'light', 'dark']
  var systemThemeQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null

  function normalizeTheme(theme) {
    return allowedThemes.indexOf(theme) === -1 ? 'system' : theme
  }

  function currentSystemTheme() {
    return systemThemeQuery && systemThemeQuery.matches ? 'dark' : 'light'
  }

  function getCookieTheme() {
    var match = document.cookie.match(new RegExp('(?:^|; )' + THEME_KEY + '=([^;]*)'))
    return match ? decodeURIComponent(match[1]) : null
  }

  function setCookieTheme(theme) {
    try {
      document.cookie =
        THEME_KEY +
        '=' +
        encodeURIComponent(theme) +
        '; path=/; max-age=31536000; SameSite=Lax'
    } catch (error) {
      // The current page theme is already applied even if browser persistence is blocked.
    }
  }

  function persistTheme(theme) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(THEME_KEY, theme)
      }
    } catch (error) {
      // Browser storage can be unavailable in some local/private browser contexts.
    }

    setCookieTheme(theme)
  }

  function applyTheme(theme) {
    var nextTheme = normalizeTheme(theme)
    document.documentElement.setAttribute('data-theme', nextTheme)
    document.documentElement.setAttribute('data-system-theme', currentSystemTheme())

    return nextTheme
  }

  function setTheme(theme, shouldPersist) {
    var nextTheme = applyTheme(theme)

    if (shouldPersist !== false) {
      persistTheme(nextTheme)
    }
  }

  function getStoredTheme() {
    var storedTheme = null

    try {
      storedTheme = window.localStorage ? window.localStorage.getItem(THEME_KEY) : null
    } catch (error) {
      storedTheme = null
    }

    return normalizeTheme(storedTheme || getCookieTheme() || 'system')
  }

  document.addEventListener('DOMContentLoaded', function () {
    var body = document.body
    var themeSelect = document.querySelector('[data-theme-select]')
    var sidebarToggle = document.querySelector('[data-sidebar-toggle]')
    var sidebarCloseItems = document.querySelectorAll('[data-sidebar-close], .app-sidebar a')
    var sidebarCollapse = document.querySelector('[data-sidebar-collapse]')
    var accountMenu = document.querySelector('[data-account-menu]')
    var accountToggle = document.querySelector('[data-account-toggle]')

    setTheme(getStoredTheme(), false)

    if (themeSelect) {
      themeSelect.value = getStoredTheme()
      themeSelect.addEventListener('change', function () {
        setTheme(themeSelect.value)
      })
    }

    if (systemThemeQuery) {
      var handleSystemThemeChange = function () {
        if (getStoredTheme() === 'system') {
          setTheme('system', false)
        } else {
          document.documentElement.setAttribute('data-system-theme', currentSystemTheme())
        }
      }

      if (systemThemeQuery.addEventListener) {
        systemThemeQuery.addEventListener('change', handleSystemThemeChange)
      } else if (systemThemeQuery.addListener) {
        systemThemeQuery.addListener(handleSystemThemeChange)
      }
    }

    if (sidebarToggle) {
      sidebarToggle.addEventListener('click', function () {
        body.classList.toggle('sidebar-open')
      })
    }

    sidebarCloseItems.forEach(function (item) {
      item.addEventListener('click', function () {
        body.classList.remove('sidebar-open')
      })
    })

    if (sidebarCollapse) {
      function setSidebarCollapsed(isCollapsed, shouldPersist) {
        body.classList.toggle('sidebar-collapsed', isCollapsed)
        sidebarCollapse.setAttribute('aria-label', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar')
        sidebarCollapse.setAttribute('title', isCollapsed ? 'Expand sidebar' : 'Collapse sidebar')

        if (shouldPersist === false) {
          return
        }

        try {
          if (window.localStorage) {
            window.localStorage.setItem('ruc-sidebar-collapsed', isCollapsed)
          }
        } catch (error) {
          return
        }
      }

      try {
        if (window.localStorage && window.localStorage.getItem('ruc-sidebar-collapsed') === 'true') {
          setSidebarCollapsed(true, false)
        } else {
          setSidebarCollapsed(false, false)
        }
      } catch (error) {
        setSidebarCollapsed(false, false)
      }

      sidebarCollapse.addEventListener('click', function () {
        setSidebarCollapsed(!body.classList.contains('sidebar-collapsed'))
      })
    }

    function setAccountMenuOpen(isOpen) {
      if (!accountMenu || !accountToggle) {
        return
      }

      accountMenu.classList.toggle('is-open', isOpen)

      if (accountMenu.tagName && accountMenu.tagName.toLowerCase() === 'details') {
        if (isOpen) {
          accountMenu.setAttribute('open', '')
        } else {
          accountMenu.removeAttribute('open')
        }
      }

      accountToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false')
    }

    if (accountMenu && accountToggle) {
      setAccountMenuOpen(Boolean(accountMenu.open))

      accountMenu.addEventListener('toggle', function () {
        setAccountMenuOpen(Boolean(accountMenu.open))
      })

      accountToggle.addEventListener('click', function (event) {
        event.stopPropagation()

        if (accountMenu.tagName && accountMenu.tagName.toLowerCase() === 'details') {
          window.setTimeout(function () {
            setAccountMenuOpen(Boolean(accountMenu.open))
          }, 0)
          return
        }

        setAccountMenuOpen(!accountMenu.classList.contains('is-open'))
      })

      accountMenu.addEventListener('click', function (event) {
        event.stopPropagation()
      })

      document.addEventListener('click', function () {
        setAccountMenuOpen(false)
      })

      document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
          setAccountMenuOpen(false)
        }
      })
    }
  })
})()
