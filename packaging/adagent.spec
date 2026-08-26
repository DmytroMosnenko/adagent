Name:       adagent
Version:    %{version}
Release:    1%{?dist}
Summary:    AdAgent — AI-powered OLX ad analysis SaaS
License:    Proprietary
BuildArch:  x86_64

%description
AdAgent analyzes OLX, Otomoto, and Otodom search results using AI,
ranking ads by quality and surfacing red flags. Freemium: 5 free ads,
unlimited with Pro subscription.

# ── Prep ──────────────────────────────────────────────────────────────────────
%prep
# nothing to unpack — we build in-place from SOURCES

# ── Install ───────────────────────────────────────────────────────────────────
%install
rm -rf %{buildroot}

# Application files → /opt/adagent
install -d %{buildroot}/opt/adagent
cp -r %{_sourcedir}/service       %{buildroot}/opt/adagent/service
cp -r %{_sourcedir}/templates     %{buildroot}/opt/adagent/templates
cp -r %{_sourcedir}/static        %{buildroot}/opt/adagent/static
cp -r %{_sourcedir}/prompts       %{buildroot}/opt/adagent/prompts
cp -r %{_sourcedir}/alembic       %{buildroot}/opt/adagent/alembic
cp    %{_sourcedir}/adagent_server.py   %{buildroot}/opt/adagent/
cp    %{_sourcedir}/alembic.ini         %{buildroot}/opt/adagent/
cp    %{_sourcedir}/requirements.txt    %{buildroot}/opt/adagent/

# Secrets skeleton (real values filled in post-install)
install -d %{buildroot}/opt/adagent/secrets
install -m 600 %{_sourcedir}/packaging/env %{buildroot}/opt/adagent/secrets/env

# Report storage directory placeholder
install -d %{buildroot}/opt/adagent/report_storage

# Log directory
install -d %{buildroot}/var/log/adagent

# systemd units
install -d %{buildroot}%{_unitdir}
install -m 644 %{_sourcedir}/packaging/adagent.service \
               %{buildroot}%{_unitdir}/adagent.service
install -m 644 %{_sourcedir}/packaging/adagent-migrate.service \
               %{buildroot}%{_unitdir}/adagent-migrate.service
install -m 644 %{_sourcedir}/packaging/adagent-ssl_all_certs_renew.service \
               %{buildroot}%{_unitdir}/adagent-ssl_all_certs_renew.service
install -m 644 %{_sourcedir}/packaging/adagent-ssl_all_certs_renew.timer \
               %{buildroot}%{_unitdir}/adagent-ssl_all_certs_renew.timer

# nginx config
install -d %{buildroot}/etc/nginx/conf.d
install -m 644 %{_sourcedir}/packaging/adagent-nginx.conf \
               %{buildroot}/etc/nginx/conf.d/adagent.conf

# logrotate
install -d %{buildroot}/etc/logrotate.d
install -m 644 %{_sourcedir}/packaging/adagent-logrotate \
               %{buildroot}/etc/logrotate.d/adagent

# ── Files ─────────────────────────────────────────────────────────────────────
%files
%defattr(-,adagent,adagent,-)
/opt/adagent/
%attr(700,adagent,adagent) /opt/adagent/secrets
%attr(600,adagent,adagent) /opt/adagent/secrets/env
/var/log/adagent/

%defattr(-,root,root,-)
%{_unitdir}/adagent.service
%{_unitdir}/adagent-migrate.service
%{_unitdir}/adagent-ssl_all_certs_renew.service
%{_unitdir}/adagent-ssl_all_certs_renew.timer
/etc/nginx/conf.d/adagent.conf
/etc/logrotate.d/adagent

# ── Pre-install ───────────────────────────────────────────────────────────────
%pre
# Create system user if absent
if ! id adagent &>/dev/null; then
    useradd --system --no-create-home --shell /sbin/nologin \
            --home-dir /opt/adagent adagent
fi

# ── Post-install ──────────────────────────────────────────────────────────────
%post
# Create venv and install Python deps
python3.12 -m venv /opt/adagent/venv
/opt/adagent/venv/bin/pip install --upgrade pip --quiet
/opt/adagent/venv/bin/pip install -r /opt/adagent/requirements.txt --quiet

# Install Playwright browsers into a fixed path accessible to the service user
export PLAYWRIGHT_BROWSERS_PATH=/opt/adagent/.playwright
if ! compgen -G "$PLAYWRIGHT_BROWSERS_PATH/chromium-*" > /dev/null; then
    echo "Installing Playwright browsers for user adagent..."
    if ! runuser -u adagent -- /opt/adagent/venv/bin/python -m playwright install; then
        echo "❌ Playwright install failed" >&2
        exit 1
    fi
else
    echo "Playwright browsers already installed, skipping."
fi

# Fix permissions
chown -R adagent:adagent /opt/adagent /var/log/adagent
chmod 700 /opt/adagent/secrets
chmod 600 /opt/adagent/secrets/env

# Issue TLS certificate if not already present
if [ ! -f /etc/letsencrypt/live/adagent.dimosense.com/fullchain.pem ]; then
    certbot certonly --nginx \
        -d adagent.dimosense.com \
        --non-interactive --agree-tos \
        -m amidtrader@gmail.com 2>&1 || true
fi

# Run DB migration as the service user
systemctl start adagent-migrate.service 2>&1 || true

# Reload nginx, enable & restart service
systemctl reload nginx 2>&1 || true
systemctl daemon-reload
systemctl enable adagent.service
systemctl restart adagent.service
systemctl enable adagent-ssl_all_certs_renew.timer
systemctl start  adagent-ssl_all_certs_renew.timer

echo ""
echo "===================================================================="
echo " AdAgent installed."
echo " Edit secrets:  /opt/adagent/secrets/env"
echo " Then restart:  systemctl restart adagent"
echo " Logs:          journalctl -u adagent -f"
echo "===================================================================="

# ── Pre-uninstall ─────────────────────────────────────────────────────────────
%preun
if [ $1 -eq 0 ]; then
    systemctl stop    adagent.service 2>/dev/null || true
    systemctl disable adagent.service 2>/dev/null || true
    systemctl stop    adagent-ssl_all_certs_renew.timer 2>/dev/null || true
    systemctl disable adagent-ssl_all_certs_renew.timer 2>/dev/null || true
fi

# ── Changelog ─────────────────────────────────────────────────────────────────
%changelog
* Wed Jan 01 2025 Dmytro Mosnenko <amidtrader@gmail.com> - 0.1.0-1
- Initial release
