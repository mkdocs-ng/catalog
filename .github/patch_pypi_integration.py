#!/usr/bin/env python3
"""Patch best-of-generator's pypi_integration.py to properly handle
urllib3.HTTPError for pypistats.org 429 rate-limit responses.

Problem:
  pypistats library raises urllib3.exceptions.HTTPError for HTTP 429,
  but best-of-generator only catches requests.exceptions.HTTPError
  and httpx.HTTPStatusError. So 429 errors fall through to the
  generic except Exception handler and are discarded.

Fixes:
  1. Catch urllib3.exceptions.HTTPError in the retry clause
  2. Safe 429 detection (urllib3 errors lack .response attr)
  3. 2s fixed delay between requests (pypistats limit: 30 req/min)

Usage:
  python .github/patch_pypi_integration.py
"""

import importlib.util
import pathlib

spec = importlib.util.find_spec("best_of.integrations.pypi_integration")
if spec is None or spec.origin is None:
    raise RuntimeError("best_of.integrations.pypi_integration not found")

path = pathlib.Path(spec.origin)
src = path.read_text()

# 1. Add urllib3 HTTPError import
src = src.replace(
    "from requests.exceptions import HTTPError",
    "from requests.exceptions import HTTPError\nfrom urllib3.exceptions import HTTPError as Urllib3HTTPError",
)

# 2. Catch Urllib3HTTPError in except clause
src = src.replace(
    "except (HTTPError, HTTPStatusError) as ex:",
    "except (HTTPError, HTTPStatusError, Urllib3HTTPError) as ex:",
)

# 3. Handle 429 check for urllib3 (no .response attr)
src = src.replace(
    "if ex.response.status_code == 429:",
    "if getattr(getattr(ex, 'response', None), 'status_code', None) == 429 or '429' in str(ex):",
)

# 4. Add 2s delay between requests (pypistats limit: 30 req/min)
src = src.replace(
    "project_info.pypi_monthly_downloads\n                )\n                return\n            except (HTTPError, HTTPStatusError, Urllib3HTTPError) as ex:",
    "project_info.pypi_monthly_downloads\n                )\n                time.sleep(2)  # respect pypistats 30 req/min limit\n                return\n            except (HTTPError, HTTPStatusError, Urllib3HTTPError) as ex:",
)

path.write_text(src)
print(f"Patched {path}")
