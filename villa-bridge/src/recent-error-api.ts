import type { FastifyInstance } from "fastify";
import type { RecentErrorLog } from "./recent-error-log.js";

const responseErrorMessage = (payload: unknown, statusCode: number): unknown => {
  if (typeof payload !== "string" && !Buffer.isBuffer(payload)) return `HTTP ${statusCode}`;
  try {
    const body = JSON.parse(payload.toString()) as { error?: unknown; message?: unknown };
    return body.error ?? body.message ?? `HTTP ${statusCode}`;
  } catch {
    return `HTTP ${statusCode}`;
  }
};

export const registerRecentErrorApi = (app: FastifyInstance, recentErrors: RecentErrorLog): void => {
  app.addHook("onSend", async (request, reply, payload) => {
    if (
      reply.statusCode >= 400
      && request.url.startsWith("/api/")
      && request.routeOptions.url !== "/api/debug/errors"
    ) {
      recentErrors.record({
        operation: `${request.method} ${request.routeOptions.url ?? "/api/unknown"}`,
        statusCode: reply.statusCode,
        message: responseErrorMessage(payload, reply.statusCode)
      });
    }
    return payload;
  });

  app.get("/api/debug/errors", async () => ({
    ok: true,
    enabled: recentErrors.isEnabled(),
    errors: recentErrors.list()
  }));

  app.delete("/api/debug/errors", async () => {
    recentErrors.clear();
    return {
      ok: true,
      enabled: recentErrors.isEnabled(),
      errors: []
    };
  });
};
