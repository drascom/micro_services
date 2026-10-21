#import <Foundation/Foundation.h>
#import <Network/Network.h>
#import <errno.h>
#import <string.h>

// Return values are part of the Rust FFI contract:
// 0 = granted, 1 = denied, 2 = pending/timed out, 3 = unavailable, 4 = internal error.
//
// macOS has no API that reports the Local Network privacy state. A browser
// reaching `ready` proves nothing (it does so before the user answers and even
// when access is denied). Results only arrive once Local Network access is
// granted, so we advertise our own Bonjour service from this (main app) process
// and browse for it, plus a meta browse (`_services._dns-sd._udp`): any result
// from either browser means granted. PolicyDenied is tracked as the *latest*
// state of each component (not sticky), because a stale PolicyDenied can be
// reported before a later grant takes effect. Every state change is written to
// `log` (one short line each) for diagnostics.
static bool inet_viewer_is_policy_denied(nw_error_t error) {
    if (error == NULL) {
        return false;
    }
    int code = nw_error_get_error_code(error);
    // kDNSServiceErr_PolicyDenied is -65570; Network.framework may also
    // surface the denial as a POSIX EACCES/EPERM.
    return code == -65570 || code == EACCES || code == EPERM;
}

static NSString *inet_viewer_error_text(nw_error_t error) {
    if (error == NULL) {
        return @"-";
    }
    return [NSString stringWithFormat:@"%d:%d", (int)nw_error_get_error_domain(error),
                                      nw_error_get_error_code(error)];
}

int inet_viewer_local_network_probe(double timeout_seconds, char *log, size_t log_len) {
    if (log != NULL && log_len > 0) {
        log[0] = '\0';
    }
    if (@available(macOS 11.0, *)) {
        const char *service_type = "_inet-viewer._tcp";
        NSString *instance = nil;
        @autoreleasepool {
            instance = [NSString
                stringWithFormat:@"inet-viewer-probe-%@", [[NSUUID UUID] UUIDString]];
        }

        nw_parameters_t parameters = nw_parameters_create_secure_tcp(
            NW_PARAMETERS_DISABLE_PROTOCOL, NW_PARAMETERS_DEFAULT_CONFIGURATION);
        if (parameters == NULL) {
            return 4;
        }
        nw_parameters_set_include_peer_to_peer(parameters, false);

        nw_listener_t listener = nw_listener_create(parameters);
        nw_advertise_descriptor_t advertise = nw_advertise_descriptor_create_bonjour_service(
            instance.UTF8String, service_type, "local.");
        nw_browse_descriptor_t descriptor =
            nw_browse_descriptor_create_bonjour_service(service_type, "local.");
        nw_browse_descriptor_t meta_descriptor =
            nw_browse_descriptor_create_bonjour_service("_services._dns-sd._udp", "local.");
        if (listener == NULL || advertise == NULL || descriptor == NULL ||
            meta_descriptor == NULL) {
            return 4;
        }
        nw_browser_t browser = nw_browser_create(descriptor, parameters);
        nw_browser_t meta_browser = nw_browser_create(meta_descriptor, parameters);
        if (browser == NULL || meta_browser == NULL) {
            return 4;
        }

        dispatch_queue_t queue = dispatch_queue_create(
            "com.drascom.inet-viewer.local-network-probe", DISPATCH_QUEUE_SERIAL);
        dispatch_semaphore_t finished = dispatch_semaphore_create(0);
        NSDate *started = [NSDate date];
        NSMutableString *lines = [NSMutableString string];
        // All state below is only touched on `queue`.
        __block int result = 2;
        __block bool completed = false;
        __block bool listener_denied = false;
        __block bool browser_denied = false;
        __block bool meta_denied = false;
        __block int failures = 0;
        void (^note)(NSString *) = ^(NSString *line) {
            [lines appendFormat:@"+%.2fs %@\n", -[started timeIntervalSinceNow], line];
        };
        void (^finish)(int) = ^(int value) {
            if (!completed) {
                result = value;
                completed = true;
                dispatch_semaphore_signal(finished);
            }
        };
        // Only report "unavailable" once every component has failed.
        void (^component_failed)(void) = ^{
            failures += 1;
            if (failures >= 3) {
                finish(3);
            }
        };

        nw_listener_set_queue(listener, queue);
        nw_listener_set_advertise_descriptor(listener, advertise);
        nw_listener_set_new_connection_handler(listener, ^(nw_connection_t connection) {
            nw_connection_cancel(connection);
        });
        nw_listener_set_state_changed_handler(listener, ^(nw_listener_state_t state,
                                                           nw_error_t error) {
            note([NSString stringWithFormat:@"listener state=%d err=%@ port=%u", (int)state,
                                            inet_viewer_error_text(error),
                                            (unsigned)nw_listener_get_port(listener)]);
            listener_denied = inet_viewer_is_policy_denied(error);
            if (state == nw_listener_state_failed && !listener_denied) {
                component_failed();
            }
        });

        nw_browser_browse_results_changed_handler_t (^results_handler)(NSString *) =
            ^nw_browser_browse_results_changed_handler_t(NSString *name) {
                return ^(nw_browse_result_t old_result, nw_browse_result_t new_result,
                         bool batch_complete) {
                    (void)old_result;
                    (void)batch_complete;
                    if (new_result != NULL) {
                        nw_endpoint_t endpoint = nw_browse_result_copy_endpoint(new_result);
                        const char *service_name =
                            endpoint ? nw_endpoint_get_bonjour_service_name(endpoint) : NULL;
                        note([NSString stringWithFormat:@"%@ result name=%s", name,
                                                        service_name ? service_name : "?"]);
                        finish(0);
                    }
                };
            };
        nw_browser_set_queue(browser, queue);
        nw_browser_set_browse_results_changed_handler(browser, results_handler(@"browser"));
        nw_browser_set_state_changed_handler(browser, ^(nw_browser_state_t state,
                                                         nw_error_t error) {
            note([NSString stringWithFormat:@"browser state=%d err=%@", (int)state,
                                            inet_viewer_error_text(error)]);
            browser_denied = inet_viewer_is_policy_denied(error);
            if (state == nw_browser_state_failed && !browser_denied) {
                component_failed();
            }
        });
        nw_browser_set_queue(meta_browser, queue);
        nw_browser_set_browse_results_changed_handler(meta_browser, results_handler(@"meta"));
        nw_browser_set_state_changed_handler(meta_browser, ^(nw_browser_state_t state,
                                                              nw_error_t error) {
            note([NSString stringWithFormat:@"meta state=%d err=%@", (int)state,
                                            inet_viewer_error_text(error)]);
            meta_denied = inet_viewer_is_policy_denied(error);
            if (state == nw_browser_state_failed && !meta_denied) {
                component_failed();
            }
        });

        nw_listener_start(listener);
        nw_browser_start(browser);
        nw_browser_start(meta_browser);

        int64_t nanos = (int64_t)(timeout_seconds * (double)NSEC_PER_SEC);
        dispatch_semaphore_wait(finished, dispatch_time(DISPATCH_TIME_NOW, nanos));
        __block int final_result = 2;
        dispatch_sync(queue, ^{
            if (!completed) {
                // Denied only when a browser still reports PolicyDenied at the deadline.
                result = (browser_denied || meta_denied) ? 1 : 2;
                completed = true;
                note([NSString stringWithFormat:@"deadline listener_denied=%d browser_denied=%d "
                                                @"meta_denied=%d",
                                                listener_denied, browser_denied, meta_denied]);
            }
            final_result = result;
            note([NSString stringWithFormat:@"result=%d", final_result]);
            nw_browser_cancel(browser);
            nw_browser_cancel(meta_browser);
            nw_listener_cancel(listener);
            if (log != NULL && log_len > 0) {
                strlcpy(log, lines.UTF8String, log_len);
            }
        });
        return final_result;
    }
    return 3;
}
