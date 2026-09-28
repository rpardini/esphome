# rpardini's insane ESPHome-on-Linux and ESPHome-on-AppleMacOs stuff via Claude


## Linux

- Tested on Debian Trixie arm64 (Rockchip rk3588)
- Can render the display directly to KMS/DRM (no X11 or Wayland needed); `unset DISPLAY`, `export SDL_VIDEODRIVER=KMSDRM`
- Uses the default ALSA sink for Sendspin audio

```bash
# Install dependencies for building ESPHome on Linux
sudo apt-get update
sudo apt-get install git python3-dev ccache avahi-daemon libavahi-client-dev pkgconf portaudio19-dev libsdl2-dev build-essential
git clone --branch=hwmon-host https://github.com/rpardini/esphome.git
cd esphome
script/setup
python3 script/run-in-env.py esphome run linux-sendspin-hwmon-i2c-sdl-avahi.yaml
```

## MacOS

- Tested on MacOS Tahoe 26.6.2 arm64 (M4)

```bash
git clone --branch=hwmon-host https://github.com/rpardini/esphome.git
cd esphome
script/setup
python3 script/run-in-env.py esphome run linux-sendspin-hwmon-i2c-sdl-avahi.yaml
```
