"""Transparent OMP hook for Playwright to use system /usr/bin/chromium and allow top-level local targets without modifying upstream scripts."""
import os

try:
    from playwright.sync_api._generated import BrowserType

    _orig_launch = BrowserType.launch

    def _omp_launch(self, *args, **kwargs):
        if not kwargs.get("executable_path"):
            sys_chromium = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH", "/usr/bin/chromium")
            if os.path.isfile(sys_chromium) and os.access(sys_chromium, os.X_OK):
                kwargs["executable_path"] = sys_chromium
        extra_args = ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]
        current_args = list(kwargs.get("args") or [])
        for flag in extra_args:
            if flag not in current_args:
                current_args.append(flag)
        kwargs["args"] = current_args
        return _orig_launch(self, *args, **kwargs)

    BrowserType.launch = _omp_launch
except Exception:
    pass

try:
    import url_safety

    _orig_route_handler = url_safety.make_safe_playwright_route_handler

    def _omp_make_route_handler(blocked_resource_types=None):
        orig_handler = _orig_route_handler(blocked_resource_types)

        def handler(route, request):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(request.url)
                if parsed.scheme in ("http", "https") and parsed.hostname:
                    port = parsed.port or (80 if parsed.scheme == "http" else 443)
                    if url_safety._is_allowlisted_local_target(url_safety.normalize_hostname(parsed.hostname), port):
                        if request.is_navigation_request() or getattr(request, "resource_type", "") == "document":
                            route.continue_()
                            return
            except Exception:
                pass
            return orig_handler(route, request)

        return handler

    url_safety.make_safe_playwright_route_handler = _omp_make_route_handler
except Exception:
    pass
