# Third-Party Notices

This project bundles no third-party source code, but it depends on the
following open-source packages at runtime (installed from PyPI):

---

## lunar-python 1.4.8

- Project: https://github.com/6tail/lunar-python
- PyPI: https://pypi.org/project/lunar-python/
- License: MIT License
- Copyright (c) 2020 6tail

```
MIT License

Copyright (c) 2020 6tail

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## tzdata 2026.5

- Project: https://github.com/python/tzdata
- PyPI: https://pypi.org/project/tzdata/
- License: Apache License 2.0 (packaging); bundled IANA time zone data is public domain
- Copyright: Python Software Foundation and contributors

This package provides the IANA time zone database consumed by the standard
library `zoneinfo` module. It is distributed under the Apache License 2.0;
the underlying tz database itself is in the public domain. Full license text:
https://github.com/python/tzdata/blob/master/LICENSE

---

## cryptography 50.0.2

- Project: https://github.com/pyca/cryptography
- PyPI: https://pypi.org/project/cryptography/
- License: Apache License 2.0 OR BSD-3-Clause (dual-licensed)
- Used for Fernet symmetric encryption of order private data.
- Transitive: cffi 2.1.1 (MIT), pycparser 3.0 (BSD-3-Clause).

---

## psycopg[binary] 3.3.6

- Project: https://github.com/psycopg/psycopg
- PyPI: https://pypi.org/project/psycopg/
- License: GNU LGPL v3.0 (psycopg and psycopg-binary 3.3.6)
- PostgreSQL client for the planned Railway PostgreSQL order store.
- The bundled libpq (in psycopg-binary) is under the PostgreSQL License.
- LGPL is used via dynamic linking; this project does not modify the library.

---

## korean_lunar_calendar 0.4.0

- Project: https://github.com/usingsky/korean_lunar_calendar_py
- PyPI: https://pypi.org/project/korean-lunar-calendar/
- License: MIT License
- Copyright (c) Jinil Lee (usingsky)

```
MIT License

Copyright (c) usingsky (Jinil Lee)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
