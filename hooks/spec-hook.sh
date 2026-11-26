#!/bin/sh

payload=$(cat) || exit 0
directory=${0%/*}
probe=$(printf '%s' "$payload" | awk -f "$directory/spec-probe.awk") || probe='!'
case "$probe" in
  C*) target=${probe#C}; target=${target:-"$PWD"} ;;
  *) target='' ;;
esac
if [ -n "$target" ]; then
  case "$target" in
    /*) resolved=$(cd -P "$target" 2>/dev/null && pwd -P) || resolved='' ;;
    *) resolved='' ;;
  esac
  if [ -n "$resolved" ]; then
    while :; do
      if [ -e "$resolved/.git" ]; then
        [ -e "$resolved/.spec" ] || exit 0
        break
      fi
      [ "$resolved" != / ] || exit 0
      resolved=${resolved%/*}
      [ -n "$resolved" ] || resolved=/
    done
  fi
fi
printf '%s\n' "$payload" | exec python3 "$directory/spec_hook.py" "$@"
