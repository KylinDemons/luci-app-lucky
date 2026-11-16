# SPDX-License-Identifier: MIT

include $(TOPDIR)/rules.mk

PKG_NAME:=lucky
PKG_VERSION:=3.8.1
PKG_RELEASE:=1

PKG_LICENSE:=MIT
PKG_LICENSE_FILES:=LICENSE
PKG_MAINTAINER:=GDY666 <gdy666@foxmail.com>

LUCKY_ARCH:=

ifeq ($(ARCH),aarch64)
  LUCKY_ARCH:=arm64
endif
ifeq ($(ARCH),arm)
  ifeq ($(CONFIG_arm_v7),y)
    LUCKY_ARCH:=armv7
  else ifeq ($(CONFIG_arm_v6),y)
    LUCKY_ARCH:=armv6
  else ifneq ($(filter arm_arm926ej-s arm_xscale,$(ARCH_PACKAGES)),)
    LUCKY_ARCH:=armv5
  endif
endif
ifeq ($(ARCH),i386)
  LUCKY_ARCH:=i386
endif
ifeq ($(ARCH),x86_64)
  LUCKY_ARCH:=x86_64
endif
ifeq ($(ARCH),riscv64)
  LUCKY_ARCH:=riscv64
endif
ifeq ($(ARCH),mips)
  ifeq ($(CONFIG_SOFT_FLOAT),y)
    LUCKY_ARCH:=mips_softfloat
  else
    LUCKY_ARCH:=mips_hardfloat
  endif
endif
ifeq ($(ARCH),mipsel)
  ifeq ($(CONFIG_SOFT_FLOAT),y)
    LUCKY_ARCH:=mipsle_softfloat
  else
    LUCKY_ARCH:=mipsle_hardfloat
  endif
endif

PKG_SOURCE:=lucky_$(PKG_VERSION)_Linux_$(LUCKY_ARCH)_wanji.tar.gz
PKG_SOURCE_URL:=https://release.66666.host/v$(PKG_VERSION)/$(PKG_VERSION)_wanji
# SHA-256 values from the upstream Wanji checksums.txt for PKG_VERSION.
LUCKY_SHA256_arm64:=7cabc0fc3dd9e6bd1b9c2304cf412f0dbf2622b9c197e0261322a2f4878c1fbb
LUCKY_SHA256_armv5:=070514acf83a6cd6c5abf8d38a4b807686d78dcaefada58051f1a76341cb54f9
LUCKY_SHA256_armv6:=f7901bfd535a838b10a5d7fbd918d84af93127b3d1d77a856b5ac9c518b9c763
LUCKY_SHA256_armv7:=abac8663c0299f0cf83c2c2c27d3e2723aa1acd8d8b330f66f850f83fbbe2662
LUCKY_SHA256_i386:=f2fcd8f56ea813e33d5db1fbc3dc36472d8032d098d2a83ef2d707f870fed56a
LUCKY_SHA256_mips_hardfloat:=77881a7739ac67aa091ad5dce9fdb5012032c710a872d232444ea62571ea882b
LUCKY_SHA256_mips_softfloat:=add463d09dce1a1cf2779a03f3585b5da9370016a2f9970e05f1f5818a3896f5
LUCKY_SHA256_mipsle_hardfloat:=39201ad4fa4992e16d3e7755c2c77f47e8c5c2f68fd4bc94a2d04442f10a3408
LUCKY_SHA256_mipsle_softfloat:=611703d1905ae312a2df8c459a70574376504f1a4747ea3def0d78314d074d17
LUCKY_SHA256_riscv64:=b2fe3b4a4f9809353571381e75db6668a4172766589398b61fba0124f7e6dce1
LUCKY_SHA256_x86_64:=c81713a6260f9b6d6fb2e7a1a28494f3ff0de0b1e2b0bb53d7694c626b9c435d
PKG_HASH:=$(LUCKY_SHA256_$(LUCKY_ARCH))
PKG_BUILD_DIR:=$(BUILD_DIR)/$(PKG_NAME)-$(PKG_VERSION)

# Preserve the packed upstream executable, including its appended data.
RSTRIP:=:

include $(INCLUDE_DIR)/package.mk

define Package/lucky
  SECTION:=net
  CATEGORY:=Network
  TITLE:=Lucky network utility
  DEPENDS:=@(aarch64||arm||i386||mips||mipsel||riscv64||x86_64) @(!TARGET_gemini)
  URL:=https://github.com/gdy666/lucky
endef

define Package/lucky/description
 Lucky provides IPv4/IPv6 port forwarding, DDNS, Wake-on-LAN,
 reverse proxy, ACME automation and related network services.
endef

define Package/lucky/conffiles
/etc/config/lucky
endef

define Build/Prepare
	gzip -dc $(DL_DIR)/$(PKG_SOURCE) | $(HOST_TAR) -C $(PKG_BUILD_DIR) -xf -
	$(Build/Patch)
endef

define Build/Compile
endef

define Package/lucky/install
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/lucky $(1)/usr/bin/lucky
	$(INSTALL_DIR) $(1)/usr/libexec
	$(INSTALL_BIN) ./files/lucky-call $(1)/usr/libexec/lucky-call
	$(INSTALL_BIN) ./files/lucky-watchdog $(1)/usr/libexec/lucky-watchdog
	$(INSTALL_DIR) $(1)/usr/share/lucky
	echo '$(LUCKY_ARCH)' > $(1)/usr/share/lucky/arch
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_BIN) ./files/lucky.init $(1)/etc/init.d/lucky
	$(INSTALL_DIR) $(1)/etc/config
	$(INSTALL_CONF) ./files/luckyuci $(1)/etc/config/lucky
endef

define Package/lucky/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || /usr/libexec/lucky-watchdog sync
endef

define Package/lucky/prerm
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ] && [ "$${PKG_UPGRADE}" != 1 ]; then
	/etc/init.d/lucky stop
	/usr/libexec/lucky-watchdog remove
fi
endef

$(eval $(call BuildPackage,lucky))
