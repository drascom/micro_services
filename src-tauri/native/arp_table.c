#include <sys/types.h>
#include <sys/socket.h>
#include <sys/sysctl.h>
#include <net/if.h>
#include <net/if_dl.h>
#include <net/route.h>
#include <netinet/in.h>
#include <netinet/if_ether.h>
#include <arpa/inet.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define INET_VIEWER_ROUNDUP(a) \
    ((a) > 0 ? (1 + (((a) - 1) | (sizeof(uint32_t) - 1))) : sizeof(uint32_t))

// Reads the kernel ARP table in-process (same sysctl as `arp -an`) and writes
// one `arp -an` style line ("? (ip) at mac on ifname ifscope [ethernet]") per
// complete entry into `out`. Running in the app process keeps the read under
// the app's Local Network permission (a child `arp` may get a filtered table).
// Returns 0 or an errno; `entries` receives the number of entries written.
int inet_viewer_arp_table(char *out, size_t out_len, int *entries) {
    int mib[6] = {CTL_NET, PF_ROUTE, 0, AF_INET, NET_RT_FLAGS, RTF_LLINFO};
    size_t needed = 0;
    if (entries != NULL) {
        *entries = 0;
    }
    if (out == NULL || out_len == 0) {
        return EINVAL;
    }
    out[0] = '\0';
    if (sysctl(mib, 6, NULL, &needed, NULL, 0) < 0) {
        return errno;
    }
    if (needed == 0) {
        return 0;
    }
    needed += needed / 8;
    char *buffer = malloc(needed);
    if (buffer == NULL) {
        return ENOMEM;
    }
    if (sysctl(mib, 6, buffer, &needed, NULL, 0) < 0) {
        int error = errno;
        free(buffer);
        return error;
    }
    size_t used = 0;
    int count = 0;
    for (char *next = buffer; next < buffer + needed;) {
        struct rt_msghdr *rtm = (struct rt_msghdr *)next;
        if (rtm->rtm_msglen == 0) {
            break;
        }
        next += rtm->rtm_msglen;
        struct sockaddr_in *sin = (struct sockaddr_in *)(rtm + 1);
        struct sockaddr_dl *sdl =
            (struct sockaddr_dl *)((char *)sin + INET_VIEWER_ROUNDUP(sin->sin_len));
        if (sin->sin_family != AF_INET || sdl->sdl_family != AF_LINK || sdl->sdl_alen != 6) {
            continue;
        }
        const unsigned char *mac = (const unsigned char *)LLADDR(sdl);
        char ip[INET_ADDRSTRLEN];
        char ifname[IF_NAMESIZE] = "?";
        if (inet_ntop(AF_INET, &sin->sin_addr, ip, sizeof(ip)) == NULL) {
            continue;
        }
        if_indextoname(sdl->sdl_index, ifname);
        int written = snprintf(out + used, out_len - used,
                               "? (%s) at %02x:%02x:%02x:%02x:%02x:%02x on %s ifscope [ethernet]\n", ip, mac[0], mac[1],
                               mac[2], mac[3], mac[4], mac[5], ifname);
        if (written < 0 || (size_t)written >= out_len - used) {
            break;
        }
        used += (size_t)written;
        count += 1;
    }
    free(buffer);
    if (entries != NULL) {
        *entries = count;
    }
    return 0;
}
