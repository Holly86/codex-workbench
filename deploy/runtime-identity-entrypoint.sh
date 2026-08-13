#!/bin/sh
set -eu

runtime_uid=$(id -u)
runtime_gid=$(id -g)
runtime_home=/home/workbench
runtime_root=$(mktemp -d "${TMPDIR:-/tmp}/workbench-nss.XXXXXX")
passwd_tmp="$runtime_root/passwd.tmp"
group_tmp="$runtime_root/group.tmp"
passwd_file="$runtime_root/passwd"
group_file="$runtime_root/group"

umask 077
cp /etc/passwd "$passwd_tmp"
cp /etc/group "$group_tmp"

if ! awk -F: -v uid="$runtime_uid" '$3 == uid { found = 1 } END { exit !found }' /etc/passwd; then
    printf 'workbench-runtime:x:%s:%s::%s:/bin/bash\n' \
        "$runtime_uid" "$runtime_gid" "$runtime_home" >> "$passwd_tmp"
fi

if ! awk -F: -v gid="$runtime_gid" '$3 == gid { found = 1 } END { exit !found }' /etc/group; then
    printf 'workbench-runtime:x:%s:\n' "$runtime_gid" >> "$group_tmp"
fi

chmod 600 "$passwd_tmp" "$group_tmp"
mv "$passwd_tmp" "$passwd_file"
mv "$group_tmp" "$group_file"

export LD_PRELOAD=/usr/local/lib/libnss_wrapper.so
export NSS_WRAPPER_PASSWD="$passwd_file"
export NSS_WRAPPER_GROUP="$group_file"

exec "$@"
