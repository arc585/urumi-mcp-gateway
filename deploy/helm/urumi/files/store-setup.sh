#!/bin/sh
# One-shot, idempotent store setup, run by a Kubernetes Job with WP-CLI.
# Every step checks the current state first, so the Job is safe to re-run on each `helm upgrade`.
set -eu
cd /var/www/html

log() { echo "[store-setup] $*"; }

log "Waiting for WordPress files (the wordpress container copies them onto the volume on first start)"
until [ -f wp-config.php ] && [ -f wp-includes/version.php ]; do sleep 3; done

log "Waiting for the database"
until wp db query "SELECT 1" >/dev/null 2>&1; do sleep 3; done

if ! wp core is-installed; then
  log "Installing WordPress"
  wp core install \
    --url="$SITE_URL" \
    --title="$STORE_TITLE" \
    --admin_user="$WP_ADMIN_USER" \
    --admin_password="$WP_ADMIN_PASSWORD" \
    --admin_email="$WP_ADMIN_EMAIL" \
    --skip-email
fi

# Keep URLs in sync with the Helm values (they change between local and production).
wp option update home "$SITE_URL" >/dev/null
wp option update siteurl "$SITE_URL" >/dev/null
# Pretty permalinks; the image already ships the matching Apache .htaccess rules.
wp rewrite structure '/%postname%/' >/dev/null

log "Theme and WooCommerce"
wp theme is-installed storefront || wp theme install storefront
wp theme activate storefront >/dev/null
wp plugin is-installed woocommerce || wp plugin install woocommerce
wp plugin is-active woocommerce || wp plugin activate woocommerce

log "Store settings"
wp option update woocommerce_currency "$STORE_CURRENCY" >/dev/null
wp option update woocommerce_default_country "$STORE_COUNTRY" >/dev/null
wp option update timezone_string "$STORE_TIMEZONE" >/dev/null
wp option update woocommerce_onboarding_profile '{"skipped":true}' --format=json >/dev/null
# New stores start in "coming soon" mode, which hides the shop from visitors.
wp option update woocommerce_coming_soon no >/dev/null
wp option update woocommerce_store_pages_only no >/dev/null
# Cash on delivery: a test-friendly payment method that needs no payment provider.
wp option update woocommerce_cod_settings \
  '{"enabled":"yes","title":"Cash on delivery","description":"Pay with cash upon delivery.","instructions":"Pay with cash upon delivery.","enable_for_methods":[],"enable_for_virtual":"yes"}' \
  --format=json >/dev/null
wp wc tool run install_pages --user="$WP_ADMIN_USER" >/dev/null

if [ "$(wp wc product list --user="$WP_ADMIN_USER" --format=count)" = "0" ]; then
  log "Creating a sample product"
  wp wc product create --user="$WP_ADMIN_USER" \
    --name="$SAMPLE_PRODUCT_NAME" --regular_price="$SAMPLE_PRODUCT_PRICE" \
    --type=simple --status=publish >/dev/null
fi

log "REST API key for the WooCommerce MCP server"
wp eval-file /setup/ensure-api-key.php

log "Done. Storefront: $SITE_URL"
