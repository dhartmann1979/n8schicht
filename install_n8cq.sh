#!/bin/bash
#
# N8CQ Installer
apt update
apt full-upgrade -y
apt install git build-essential mc zip unzip p7zip-full btop htop hostapd dnsmasq apache2 php php-fpm php-cgi php-json php-opcache php-pdo php-readline php-gd php-pear -y
wget https://github.com/JiriSko/amixer-webui/releases/download/v1.0.0-rc/amixer-webui_1.0.0-rc_all.deb
dpkg -i amixer-webui_1.0.0-rc_all.deb
