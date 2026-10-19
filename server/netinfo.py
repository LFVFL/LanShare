"""网卡地址发现与排序。

要解决的具体问题：一台机器上往往有好几个 IPv4（物理网卡、Wi-Fi、VMware、
VirtualBox、Hyper-V、WSL、Clash/v2ray 的 TUN…），二维码只能扫一个，扫错了
就是打不开；直接给 localhost 更没用——手机扫了只会访问它自己。

判定"路由器那条局域网"的信号（不依赖第三方库、不发包）：

1. 用 UDP `connect` 到公网地址：内核只是查了一次路由表、并没有真的发包，
   `getsockname()` 拿到默认路由那块网卡的地址。**但这条不能盲信**——
   装了代理（Clash/v2ray TUN）或 VPN 时，默认路由会指向虚拟网卡，
   拿到的是 198.18.x.x / 100.64.x.x 这类地址，手机根本连不上。
2. 所以先过滤掉"虚拟网段"（198.18/15 RFC2544、100.64/10 CGNAT/Tailscale、
   169.254/16 APIPA），再用网段可信度排序：192.168.* > 10.* > 172.16–31.*。
3. 同档里再排一次：以 .1 结尾的多半是路由器本身或 VMware/Hyper-V 的
   host-only 网卡，自家机器很少是 .1，所以往后放。

回环地址永远不参与推荐。
"""
import socket

# 这些地址只用于查路由表，UDP connect 不会真的发包
_PROBE_TARGETS = (('8.8.8.8', 80), ('1.1.1.1', 80), ('223.5.5.5', 80))

# 这些网段是虚拟网卡/异常地址，二维码绝对不能指向它们
_VIRTUAL_PREFIXES = ('198.18.', '198.19.', '169.254.')
_VIRTUAL_100_64 = 64, 127           # 100.64.0.0/10 的第二段范围
_PRIVATE_PREFIXES = ('192.168.', '10.')
_DOT_ONE_TAIL = '.1'


def primary_ip():
    """默认路由所在网卡的 IPv4（可能是虚拟网卡，交给 local_ips 判断）；拿不到返回 None。"""
    for target in _PROBE_TARGETS:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(0.2)
            sock.connect(target)
            ip = sock.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
        except OSError:
            continue
        finally:
            if sock is not None:
                sock.close()
    return None


def hostname_ips():
    """主机名能解析出的全部 IPv4（去重、去回环）。"""
    out = []
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM):
            ip = info[4][0]
            if ip and not ip.startswith('127.') and ip not in out:
                out.append(ip)
    except OSError:
        pass
    return out


def is_virtual(ip):
    """虚拟网卡/异常地址：代理 TUN、VPN、APIPA。"""
    if ip.startswith(_VIRTUAL_PREFIXES):
        return True
    try:
        parts = ip.split('.')
        return parts[0] == '100' and _VIRTUAL_100_64[0] <= int(parts[1]) <= _VIRTUAL_100_64[1]
    except (IndexError, ValueError):
        return True


def is_private(ip):
    if ip.startswith(_PRIVATE_PREFIXES):
        return True
    if ip.startswith('172.'):
        try:
            return 16 <= int(ip.split('.')[1]) <= 31
        except (IndexError, ValueError):
            return False
    return False


def _tier(ip):
    """越小越像"路由器那条局域网"。"""
    if is_virtual(ip):
        return 90
    if ip.startswith(_PRIVATE_PREFIXES):
        return 10 if ip.startswith('192.168.') else 20
    if ip.startswith('172.'):
        return 30 if is_private(ip) else 50
    return 50


def rank_ips(ips, primary=None):
    """按可信度排序并去重；primary（默认路由地址）在非虚拟时插到最前面。"""
    seen = []
    for ip in ips:
        if ip and not ip.startswith('127.') and ip not in seen:
            seen.append(ip)
    order = {ip: i for i, ip in enumerate(seen)}
    ranked = sorted(seen, key=lambda ip: (_tier(ip), ip.endswith(_DOT_ONE_TAIL), order[ip]))
    if primary and primary in ranked and not is_virtual(primary) and _tier(primary) <= 30:
        ranked.remove(primary)
        ranked.insert(0, primary)
    return ranked


def local_ips():
    """推荐顺序的 IPv4 列表；最可能被手机访问到的排第一，兜底 127.0.0.1。"""
    primary = primary_ip()
    ranked = rank_ips(([primary] if primary else []) + hostname_ips(), primary)
    return ranked if ranked else ['127.0.0.1']


def ip_label(ip, preferred=None):
    """给地址一个短标签，UI 上直接显示。"""
    if ip in ('localhost', '127.0.0.1') or ip.startswith('127.'):
        return '本机'
    if preferred and ip == preferred:
        return '推荐'
    if is_virtual(ip):
        return '异常'
    if not is_private(ip):
        return '其他'
    # 自家机器很少是 .1：多半是 VMware / Hyper-V 的 host-only 网卡或路由器本身
    return '其他网卡' if ip.endswith(_DOT_ONE_TAIL) else '局域网'
