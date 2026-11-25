# Adminer pinned to the version that runs on the real server (see docs/development.md).
FROM php:8.4-apache

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev \
    && docker-php-ext-install pgsql \
    && rm -rf /var/lib/apt/lists/*

ARG ADMINER_VERSION=6.3.1
ARG ADMINER_SHA256=a9aed04e4e68c4e46283804a69ee70cad4fbf23203b46470b760868b2fd9bd76

RUN curl -fsSL -o /var/www/html/adminer.php \
        "https://github.com/vrana/adminer/releases/download/v${ADMINER_VERSION}/adminer-${ADMINER_VERSION}.php" \
    && echo "${ADMINER_SHA256}  /var/www/html/adminer.php" | sha256sum -c - \
    && chown www-data:www-data /var/www/html/adminer.php
