from __future__ import annotations

from textwrap import dedent


BUILTIN_MONITOR_COMMANDS = [
    {
        "name": "识别 Linux 系统",
        "description": "识别发行版、内核、架构、初始化系统和可用包管理器。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "hostnamectl"
            hostnamectl 2>/dev/null | sed -n '1,12p' || true
            section "os-release"
            cat /etc/os-release 2>/dev/null || true
            section "kernel"
            uname -a
            section "architecture"
            uname -m
            section "pid1"
            ps -p 1 -o pid=,comm=,args=
            section "package-manager"
            found=0
            for pm in apt apt-get dnf yum zypper apk pacman; do
              if command -v "$pm" >/dev/null 2>&1; then
                echo "$pm"
                found=1
              fi
            done
            [ "$found" -eq 1 ] || echo "unknown"
            """
        ).strip(),
    },
    {
        "name": "服务器运行状态总览",
        "description": "查看负载、内存、磁盘、关键进程和服务失败状态。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "uptime"
            uptime
            section "loadavg"
            cat /proc/loadavg 2>/dev/null || true
            section "memory"
            free -h 2>/dev/null || vmstat -s 2>/dev/null || true
            section "root-disk"
            df -hP /
            section "inode"
            df -iP / 2>/dev/null || true
            section "top-cpu"
            ps -eo pid,ppid,user,comm,%cpu,%mem --sort=-%cpu 2>/dev/null | head -n 12
            section "top-memory"
            ps -eo pid,ppid,user,comm,%mem,%cpu --sort=-%mem 2>/dev/null | head -n 12
            section "failed-services"
            if command -v systemctl >/dev/null 2>&1; then
              systemctl --failed --no-pager --no-legend 2>/dev/null || true
            else
              service --status-all 2>/dev/null | sed -n '1,40p' || true
            fi
            section "login-users"
            who -a 2>/dev/null || who 2>/dev/null || true
            """
        ).strip(),
    },
    {
        "name": "网络与监听服务检查",
        "description": "查看 IP、路由、DNS、监听端口和基础外网解析能力。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "ip-address"
            ip -brief addr 2>/dev/null || ifconfig -a 2>/dev/null || true
            section "route"
            ip route 2>/dev/null || netstat -rn 2>/dev/null || true
            section "resolv"
            cat /etc/resolv.conf 2>/dev/null || true
            section "listen-ports"
            ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null || true
            section "connectivity"
            (ping -c 1 -W 1 223.5.5.5 >/dev/null 2>&1 && echo "ping: ok") || echo "ping: failed"
            section "dns-lookup"
            (getent hosts openai.com 2>/dev/null || nslookup openai.com 2>/dev/null || host openai.com 2>/dev/null) | sed -n '1,20p'
            """
        ).strip(),
    },
    {
        "name": "SSH 与账户安全基线",
        "description": "检查 SSH 配置、UID 0 账户、sudo 组和密码策略。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            run_root(){
              if [ "$(id -u)" -eq 0 ]; then
                sh -lc "$1"
              elif command -v sudo >/dev/null 2>&1; then
                sudo -n sh -lc "$1" 2>/dev/null || sh -lc "$1"
              else
                sh -lc "$1"
              fi
            }
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "ssh-config"
            run_root 'for f in /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf; do [ -f "$f" ] || continue; echo "## $f"; grep -E "^(PermitRootLogin|PasswordAuthentication|PubkeyAuthentication|MaxAuthTries|PermitEmptyPasswords|ClientAliveInterval|LoginGraceTime|X11Forwarding|AllowUsers|AllowGroups)" "$f" 2>/dev/null || true; done'
            section "uid-0-accounts"
            run_root 'awk -F: '\''$3==0 {print $1 ":" $7}'\'' /etc/passwd'
            section "sudo-groups"
            getent group sudo 2>/dev/null || getent group wheel 2>/dev/null || echo "sudo/wheel group not found"
            section "password-policy"
            run_root 'grep -E "^(PASS_MAX_DAYS|PASS_MIN_DAYS|PASS_WARN_AGE|UMASK)" /etc/login.defs 2>/dev/null || true; grep -E "^(minlen|minclass|retry|enforce_for_root)" /etc/security/pwquality.conf 2>/dev/null || true'
            section "empty-password-users"
            run_root 'awk -F: '\''$2=="" {print $1}'\'' /etc/shadow 2>/dev/null || true'
            """
        ).strip(),
    },
    {
        "name": "防火墙与暴露面检查",
        "description": "检查 firewalld、ufw、nftables、iptables 和暴露端口。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            run_root(){
              if [ "$(id -u)" -eq 0 ]; then
                sh -lc "$1"
              elif command -v sudo >/dev/null 2>&1; then
                sudo -n sh -lc "$1" 2>/dev/null || sh -lc "$1"
              else
                sh -lc "$1"
              fi
            }
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "firewalld"
            systemctl is-enabled firewalld 2>/dev/null || true
            firewall-cmd --state 2>/dev/null || true
            firewall-cmd --list-all 2>/dev/null || true
            section "ufw"
            ufw status verbose 2>/dev/null || true
            section "nftables"
            run_root 'nft list ruleset 2>/dev/null | sed -n "1,120p"' || true
            section "iptables"
            run_root 'iptables -S 2>/dev/null | sed -n "1,80p"; ip6tables -S 2>/dev/null | sed -n "1,80p"' || true
            section "listening-ports"
            ss -tulpn 2>/dev/null || netstat -tulpn 2>/dev/null || true
            """
        ).strip(),
    },
    {
        "name": "等保基线快速检查",
        "description": "检查时间同步、审计、日志、SELinux/AppArmor、core dump 和挂载基线。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            run_root(){
              if [ "$(id -u)" -eq 0 ]; then
                sh -lc "$1"
              elif command -v sudo >/dev/null 2>&1; then
                sudo -n sh -lc "$1" 2>/dev/null || sh -lc "$1"
              else
                sh -lc "$1"
              fi
            }
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "time-sync"
            timedatectl status 2>/dev/null | sed -n '1,12p' || chronyc tracking 2>/dev/null || ntpq -pn 2>/dev/null || true
            section "auditd"
            systemctl status auditd --no-pager 2>/dev/null | sed -n '1,20p' || service auditd status 2>/dev/null || true
            section "rsyslog"
            systemctl status rsyslog --no-pager 2>/dev/null | sed -n '1,20p' || service rsyslog status 2>/dev/null || true
            section "selinux-apparmor"
            getenforce 2>/dev/null || echo "SELinux unavailable"
            aa-status 2>/dev/null | sed -n '1,20p' || echo "AppArmor unavailable"
            section "core-dump"
            run_root 'grep -R "^[^#].*hard core" /etc/security/limits.conf /etc/security/limits.d 2>/dev/null || true; sysctl fs.suid_dumpable 2>/dev/null || true'
            section "mount-baseline"
            mount 2>/dev/null | grep -E " /( |type)|/tmp|/var|/home" | sed -n '1,30p' || true
            """
        ).strip(),
    },
    {
        "name": "软件补丁与安全更新检查",
        "description": "识别包管理器并检查可升级软件包数量，适配常见 Linux 发行版。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "kernel"
            uname -r
            section "package-updates"
            if command -v apt >/dev/null 2>&1; then
              apt list --upgradable 2>/dev/null | sed '1d' | sed -n '1,50p'
              echo "count=$(apt list --upgradable 2>/dev/null | sed '1d' | wc -l)"
            elif command -v dnf >/dev/null 2>&1; then
              dnf check-update -q 2>/dev/null | awk 'NF && $1 !~ /^(Last|Obsoleting|Security:)/ {print}' | sed -n '1,50p'
              echo "count=$(dnf check-update -q 2>/dev/null | awk 'NF && $1 !~ /^(Last|Obsoleting|Security:)/ {c++} END{print c+0}')"
            elif command -v yum >/dev/null 2>&1; then
              yum check-update -q 2>/dev/null | awk 'NF && $1 !~ /^(Loaded|Security:|Obsoleting)/ {print}' | sed -n '1,50p'
              echo "count=$(yum check-update -q 2>/dev/null | awk 'NF && $1 !~ /^(Loaded|Security:|Obsoleting)/ {c++} END{print c+0}')"
            elif command -v zypper >/dev/null 2>&1; then
              zypper list-updates 2>/dev/null | sed -n '1,60p'
            elif command -v apk >/dev/null 2>&1; then
              apk version 2>/dev/null | grep '<' | sed -n '1,50p'
              echo "count=$(apk version 2>/dev/null | grep -c '<' || true)"
            elif command -v pacman >/dev/null 2>&1; then
              checkupdates 2>/dev/null | sed -n '1,50p'
              echo "count=$(checkupdates 2>/dev/null | wc -l)"
            else
              echo "unsupported package manager"
            fi
            """
        ).strip(),
    },
    {
        "name": "计划任务与持久化检查",
        "description": "检查系统计划任务、用户 crontab、systemd timer 和 rc.local。",
        "scope_all_servers": True,
        "is_builtin": True,
        "command": dedent(
            """
            export LC_ALL=C
            run_root(){
              if [ "$(id -u)" -eq 0 ]; then
                sh -lc "$1"
              elif command -v sudo >/dev/null 2>&1; then
                sudo -n sh -lc "$1" 2>/dev/null || sh -lc "$1"
              else
                sh -lc "$1"
              fi
            }
            section(){ printf "\\n[%s]\\n" "$1"; }
            section "system-cron"
            run_root 'for f in /etc/crontab /etc/cron.d/*; do [ -f "$f" ] || continue; echo "## $f"; sed -n "1,120p" "$f"; done'
            section "user-cron"
            run_root 'for u in $(cut -d: -f1 /etc/passwd); do cr=$(crontab -l -u "$u" 2>/dev/null); [ -n "$cr" ] && echo "## $u" && echo "$cr"; done'
            section "systemd-timers"
            systemctl list-timers --all --no-pager 2>/dev/null | sed -n '1,50p' || true
            section "rc-local"
            run_root 'sed -n "1,120p" /etc/rc.local 2>/dev/null || true'
            """
        ).strip(),
    },
]
