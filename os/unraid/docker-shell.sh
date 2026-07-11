#!/bin/bash
# easily "docker exec" into a running Docker container

# for unRAID, place this script on your flash drive as /boot/custom/docker-shell
# then add this to your go script (without the leading pound sign):
# cp /boot/custom/docker-shell /usr/local/bin

# Use Docker's built-in formatting to get container names cleanly
CONTAINERS=$(docker ps --format '{{.Names}}' | sort -f)

if [ -z "$CONTAINERS" ]; then
  echo "No running Docker containers found."
  exit 1
fi

echo "Choose a Docker container:"
# Use the bash 'select' built-in to safely handle numbered menus
PS3="Enter a number: "
select CHOSEN in $CONTAINERS; do
  if [ -n "$CHOSEN" ]; then
    break
  else
    echo "Invalid option. Please try again."
  fi
done

# try running bash; if exit code 126 or 127 then try running sh
for SHELL in bash sh; do
  clear
  echo " "
  echo -e '\E[30;42m'"\033[5m    $CHOSEN - $SHELL   \033[0m"
  echo " "
  tput sgr0

  # Many unRAID containers drop privileges to PUID/PGID (LinuxServer)
  # or USER_ID/GROUP_ID (jlesage), so files created in the shell
  # are root-owned unless we do the same!
  ENV_VARS=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CHOSEN")
  PUID=$(echo "$ENV_VARS" | grep -E '^PUID=' | cut -d= -f2)
  PGID=$(echo "$ENV_VARS" | grep -E '^PGID=' | cut -d= -f2)

  # Fallback for jlesage containers (like MKVToolNix) which use USER_ID / GROUP_ID
  if [ -z "$PUID" ]; then
    PUID=$(echo "$ENV_VARS" | grep -E '^USER_ID=' | cut -d= -f2)
  fi
  if [ -z "$PGID" ]; then
    PGID=$(echo "$ENV_VARS" | grep -E '^GROUP_ID=' | cut -d= -f2)
  fi

  USER_ARG=""
  if [ -n "$PUID" ]; then
    if [ -n "$PGID" ]; then
      USER_ARG="-u $PUID:$PGID"
    else
      USER_ARG="-u $PUID"
    fi
    echo -e "  \E[32mSwitching to user $PUID${PGID:+:$PGID}\033[0m"
    echo " "
  fi

  # Note: $USER_ARG is intentionally unquoted here so it expands to either nothing or the -u flag
  docker exec $USER_ARG -it "$CHOSEN" "$SHELL"

  # You MUST save $? to a variable before checking multiple conditions!
  # Otherwise, the first [ ... ] check overwrites $? for the second check.
  EXIT_CODE=$?

  # 126 = Command invoked cannot execute
  # 127 = Command not found
  if [ $EXIT_CODE -ne 126 ] && [ $EXIT_CODE -ne 127 ]; then
    break
  fi
done
