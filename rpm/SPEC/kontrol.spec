Name: %{kontrol_name}
Version: %{kontrol_version}
Release: 1
Summary: %{kontrol_description}
License: MIT
URL: https://github.com/kevwargo/kontrol

Source0: %{kontrol_src}
Source1: %{qasync_whl}

BuildArch: noarch

BuildRequires: python3-rpm-macros
BuildRequires: python3-build
BuildRequires: python3-hatchling
BuildRequires: python3-installer
BuildRequires: systemd-rpm-macros

Requires: python3
Requires: python3-dbus-next
Requires: python3-pyqt6
Requires: python3-pyyaml
Requires: systemd

%description
%{kontrol_description}

%prep
%autosetup -p1

%build
python3 -m build --wheel --no-isolation

%install
python3 -m installer --destdir=%{buildroot} dist/%{name}-%{version}*.whl
python3 -m installer --destdir=%{buildroot} %{SOURCE1}

install -Dm644 systemd/kwinctl.service            %{buildroot}%{_userunitdir}/kwinctl.service
install -Dm644 src/kontrol/kwinctl/kwinctl.js     %{buildroot}/usr/share/kwinctl/kwinctl.js
install -Dm644 src/kontrol/kwinctl/rules.yaml     %{buildroot}/usr/share/kwinctl/rules.yaml
install -Dm644 src/kontrol/kwinctl/commands.yaml  %{buildroot}/usr/share/kwinctl/commands.yaml
install -Dm644 src/kontrol/kwinctl/overrides.yaml %{buildroot}/usr/share/kwinctl/overrides.yaml

%post
%systemd_user_post kwinctl.service

%files
%{_bindir}/kombi
%{_bindir}/konsctl
%{_bindir}/kscreen-toggle
%{_bindir}/kwinctl
%{_bindir}/kwinjs
%{_bindir}/qkvox

/usr/share/kwinctl/kwinctl.js
/usr/share/kwinctl/rules.yaml
/usr/share/kwinctl/commands.yaml
/usr/share/kwinctl/overrides.yaml

%{python3_sitelib}/%{name}
%{python3_sitelib}/%{name}-%{version}.dist-info
%{python3_sitelib}/qasync
%{python3_sitelib}/qasync*.dist-info

%{_userunitdir}/kwinctl.service
