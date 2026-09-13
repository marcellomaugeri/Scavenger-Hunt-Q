#!/bin/sh
# Run on the UNO Q as the desktop user. App Lab still starts the game.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo 'Run as the normal board user, without sudo; setup requests sudo when needed.' >&2
    exit 1
fi
for command in chromium curl xset flock python3 pw-dump pw-cli wpctl xfconf-query; do
    command -v "$command" >/dev/null
done
test -f /usr/share/xsessions/xfce.desktop
sudo -v

# Prefer HDMI on a fresh audio profile, without relying on numeric device IDs.
mkdir -p "$HOME/.config/wireplumber/wireplumber.conf.d"
cat > "$HOME/.config/wireplumber/wireplumber.conf.d/90-scavenger-hdmi.conf" <<'AUDIO'
device.profile.priority.rules = [
  {
    matches = [ { device.name = "alsa_card.platform-sound" } ]
    actions = { update-props = { priorities = [ "HDMI" ] } }
  }
]
AUDIO
python3 - <<'AUDIO_SETUP'
import json
import subprocess
import time

def objects():
    return json.loads(subprocess.check_output(['pw-dump'], timeout=5))

device = next(item for item in objects()
              if item.get('info', {}).get('props', {}).get('device.name') == 'alsa_card.platform-sound')
profile = next(item for item in device['info']['params']['EnumProfile'] if item['name'] == 'HDMI')
# save=True makes WirePlumber restore the selection after restarting.
subprocess.run(['pw-cli', 'set-param', str(device['id']), 'Profile',
                json.dumps({'index': profile['index'], 'save': True})], check=True, timeout=5)
for attempt in range(30):
    sink = next((item for item in objects() if item.get('info', {}).get('props', {}).get('node.name')
                 == 'alsa_output.platform-sound.HDMI__HDMI__sink'), None)
    if sink:
        for command, args in [('set-default', []), ('set-volume', ['0.40']), ('set-mute', ['0'])]:
            subprocess.run(['wpctl', command, str(sink['id']), *args], check=True, timeout=5)
        print('HDMI audio selected and unmuted at 40%; adjust listening volume on the TV.')
        break
    time.sleep(.2)
else:
    raise SystemExit('HDMI audio unavailable. Check the powered hub and TV, then rerun setup.')
AUDIO_SETUP

mkdir -p "$HOME/.local/bin" "$HOME/.config/autostart"
cat > "$HOME/.local/bin/scavenger-hunt-reset" <<'RESET'
#!/bin/sh
# Reset game state through the same local endpoint as the CLI.
exec curl --fail --silent --show-error --max-time 4 \
    --header 'Content-Type: application/json' \
    --data '{"command":"reset","arguments":{}}' \
    http://127.0.0.1:7000/api/command
RESET
chmod 755 "$HOME/.local/bin/scavenger-hunt-reset"

cat > "$HOME/.local/bin/scavenger-hunt-tv" <<'LAUNCHER'
#!/bin/sh
set -eu
mkdir -p "$HOME/.local/state/scavenger-hunt-q"
exec 9>"$HOME/.local/state/scavenger-hunt-q/tv.lock"
flock -n 9 || exit 0
exec >"$HOME/.local/state/scavenger-hunt-q/tv.log" 2>&1
xset s off || true
xset -dpms || true
xset s noblank || true
# The board's white button sends XF86PowerOff; configure it in this desktop session.
xfconf-query -c xfce4-keyboard-shortcuts -p /commands/custom/XF86PowerOff \
    -n -t string -s "$HOME/.local/bin/scavenger-hunt-reset"
until curl -fsS --max-time 3 http://127.0.0.1:7000/api/status >/dev/null; do
    sleep 1
done
while :; do
    # GPU page rasterization corrupts SVG/text on the UNO Q's current graphics driver.
    chromium --user-data-dir="$HOME/.config/scavenger-hunt-q-chromium" \
        --no-first-run --no-default-browser-check \
        --disable-session-crashed-bubble \
        --disable-gpu-rasterization \
        --autoplay-policy=no-user-gesture-required \
        --kiosk --start-fullscreen http://127.0.0.1:7000/ || true
    sleep 3
done
LAUNCHER
chmod 755 "$HOME/.local/bin/scavenger-hunt-tv"
cat > "$HOME/.config/autostart/scavenger-hunt-q.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Scavenger Hunt Q
Exec="$HOME/.local/bin/scavenger-hunt-tv"
Terminal=false
DESKTOP

sudo mkdir -p /etc/lightdm/lightdm.conf.d
sudo tee /etc/lightdm/lightdm.conf.d/90-scavenger-hunt-q.conf >/dev/null <<CONFIG
[Seat:*]
xserver-command=X -nocursor
autologin-user=$(id -un)
autologin-user-timeout=0
user-session=xfce
autologin-session=xfce
CONFIG
echo 'TV setup complete. Enable Run at Startup in App Lab, then restart the board.'
