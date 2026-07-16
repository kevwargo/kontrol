## 1

A clean end state would be to make `kontrol` a real Python package and let both RPM and Arch install the same artifacts. Below is one possible layout.

The idea:

* `src/kontrol/` contains all Python code.
* `pyproject.toml` defines console scripts.
* RPM uses `%pyproject_wheel` + `%pyproject_install`.
* The systemd user unit remains a data file installed by RPM.
* Shared code is imported normally (`kontrol.common`, etc.).

## Directory tree

```text
kontrol/
├── Makefile
├── README.md
├── pyproject.toml
├── LICENSE
│
├── rpm/
│   └── kontrol.spec
│
├── systemd/
│   └── kwinctl.service
│
├── src/
│   └── kontrol/
│       ├── __init__.py
│       │
│       ├── common/
│       │   ├── __init__.py
│       │   ├── dbus.py
│       │   ├── logging.py
│       │   └── config.py
│       │
│       ├── kwinctl/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   └── kwinjs.py
│       │
│       ├── konsctl/
│       │   ├── __init__.py
│       │   └── main.py
│       │
│       ├── kombi/
│       │   ├── __init__.py
│       │   └── main.py
│       │
│       └── qkvox/
│           ├── __init__.py
│           └── main.py
│
└── tests/
    └── ...
```

The old:

```text
kwinctl/kwinctl.py
konsctl/konsctl.py
kombi/kombi.py
qkbdialog/vox.py
```

become modules:

```text
src/kontrol/kwinctl/main.py
src/kontrol/konsctl/main.py
src/kontrol/kombi/main.py
src/kontrol/qkvox/main.py
```

Each executable gets a normal Python entry function:

```python
def main():
    ...
```

---

# pyproject.toml

```toml
[project]
name = "kontrol"
version = "0.5.1"
description = "A set of tools to configure Plasma 6 (Wayland) in a predictable way"
readme = "README.md"
license = { file = "LICENSE" }
requires-python = ">=3.13"

dependencies = [
    "dbus-next>=0.2.3",
    "pyqt6>=6.11.0",
    "pyyaml>=6.0.3",
    "qasync>=0.28.0",
]

[project.scripts]
kwinctl = "kontrol.kwinctI.main:main"
kwinjs-inspect = "kontrol.kwinctI.kwinjs:main"
konsctl = "kontrol.konsctl.main:main"
kombi = "kontrol.kombi.main:main"
qkvox = "kontrol.qkvox.main:main"

[build-system]
requires = [
    "hatchling",
]
build-backend = "hatchling.build"


[tool.hatch.build.targets.wheel]
packages = [
    "src/kontrol",
]


[tool.black]
line-length = 99
```

Small correction: the entry point should be:

```toml
kwinctl = "kontrol.kwinctl.main:main"
```

(not `kwinctlI`; typo above). The final section should be:

```toml
[project.scripts]
kwinctl = "kontrol.kwinctl.main:main"
kwinjs-inspect = "kontrol.kwinctl.kwinjs:main"
konsctl = "kontrol.konsctl.main:main"
kombi = "kontrol.kombi.main:main"
qkvox = "kontrol.qkvox.main:main"
```

---

# RPM spec

`rpm/kontrol.spec`

```spec
Name:           kontrol
Version:        0.5.1
Release:        1%{?dist}
Summary:        Plasma 6 configuration utilities

License:        MIT
URL:            https://example.invalid/kontrol
Source0:        %{name}-%{version}.tar.zst

BuildArch:      noarch


BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros

Requires:       python3
Requires:       python3-dbus-next
Requires:       python3-pyqt6
Requires:       python3-pyyaml
Requires:       python3-qasync
Requires:       systemd


%description
A set of tools to configure Plasma 6 (Wayland) in a predictable way.


%prep
%autosetup -p1


%generate_buildrequires
%pyproject_buildrequires


%build
%pyproject_wheel


%install
%pyproject_install

install -Dm644 systemd/kwinctl.service \
    %{buildroot}%{_userunitdir}/kwinctl.service


%check
%pyproject_check_import


%post
%systemd_user_post kwinctl.service


%postun
%systemd_user_postun_with_restart kwinctl.service


%files
%license LICENSE
%doc README.md

%{_bindir}/kwinctl
%{_bindir}/kwinjs-inspect
%{_bindir}/konsctl
%{_bindir}/kombi
%{_bindir}/qkvox

%{python3_sitelib}/kontrol/

%{_userunitdir}/kwinctl.service


%changelog
* Thu Jul 16 2026 Your Name <you@example.com> - 0.5.1-1
- Initial RPM package
```

---

# Makefile addition

Something like:

```make
RPMTOP=$(CURDIR)/rpm-build

.PHONY: build-rpm

build-rpm:
	mkdir -p $(RPMTOP)/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

	tar -caf $(RPMTOP)/SOURCES/kontrol-$(VERSION).tar.zst \
		--exclude-vcs \
		--exclude rpm-build \
		.

	cp rpm/kontrol.spec $(RPMTOP)/SPECS/

	rpmbuild \
		--define "_topdir $(RPMTOP)" \
		-ba $(RPMTOP)/SPECS/kontrol.spec
```

---

# systemd service

Move:

```text
kwinctl/kwinctl.service
```

to:

```text
systemd/kwinctl.service
```

Example:

```ini
[Unit]
Description=KWin configuration controller
After=graphical-session.target

[Service]
Type=simple
ExecStart=%{_bindir}/kwinctl

[Install]
WantedBy=default.target
```

(The `%{_bindir}` macro is only valid in RPM spec files, so in the actual installed file you would usually use `/usr/bin/kwinctl`.)

---

# Arch package impact

Your Arch `PKGBUILD` can eventually become simpler:

```bash
python -m build --wheel
python -m installer \
    --destdir="$pkgdir" \
    dist/*.whl
```

or use Arch's `python-build` / `python-installer` packages.

Then only the non-Python data file needs manual installation:

```bash
install -Dm644 systemd/kwinctl.service \
    "$pkgdir/usr/lib/systemd/user/kwinctl.service"
```

This leaves both RPM and Arch consuming the same Python packaging model.

## 2

Good question. The short answer is: **RPM does not use your development environment or `uv`; it reads `pyproject.toml` and installs the build backend declared in `build-system.requires` into an isolated build environment.**

The mechanism is the `pyproject-rpm-macros` package.

When your spec contains:

```spec
BuildRequires:  pyproject-rpm-macros
```

and:

```spec
%generate_buildrequires
%pyproject_buildrequires
```

RPM runs the macro, which:

1. Reads:

```toml
[build-system]
requires = [
    "hatchling",
]
build-backend = "hatchling.build"
```

2. Generates additional RPM build dependencies:

Conceptually it turns into something like:

```spec
BuildRequires: python3dist(hatchling)
```

3. Installs those dependencies into the RPM build environment.

Then later:

```spec
%build
%pyproject_wheel
```

does roughly:

```bash
python -m build --wheel
```

using the declared backend:

```text
hatchling.build
```

which creates:

```text
dist/kontrol-0.5.1-py3-none-any.whl
```

Then:

```spec
%install
%pyproject_install
```

installs that wheel into:

```text
BUILDROOT/
└── usr/
    ├── bin/
    │   ├── kwinctl
    │   ├── konsctl
    │   └── qkvox
    │
    └── lib/python3.13/site-packages/
        └── kontrol/
```

---

## What if `hatchling` is missing?

The build fails before compilation.

For example:

```text
ERROR: Missing build requirements:
python3dist(hatchling)
```

RPM will tell you that the generated BuildRequires cannot be satisfied.

---

## Where does `hatchling` come from?

On Fedora-like systems:

```bash
dnf install python3-hatchling
```

provides the RPM dependency:

```text
python3dist(hatchling)
```

The exact package name varies by distro.

---

## Why not put it manually?

You *can* write:

```spec
BuildRequires: python3-hatchling
```

but the recommended style is:

```spec
%generate_buildrequires
%pyproject_buildrequires
```

because if you later change:

```toml
[build-system]
requires = [
    "hatchling",
    "hatch-vcs",
]
```

the RPM dependencies update automatically.

---

## One subtle point

`pyproject-rpm-macros` is not the build backend. It is just RPM glue.

The chain is:

```
kontrol.spec
      |
      v
pyproject-rpm-macros
      |
      reads
      |
pyproject.toml
      |
      discovers
      |
hatchling
      |
      builds
      |
wheel
      |
      installs
      |
RPM payload
```

So your `pyproject.toml` remains the single source of truth for Python build requirements.
