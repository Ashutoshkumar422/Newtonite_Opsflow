import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => vi.restoreAllMocks());

describe("api()", () => {
  it("retries a network failure with the SAME idempotency key", async () => {
    const keys: (string | null)[] = [];
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      keys.push(new Headers(init?.headers).get("Idempotency-Key"));
      if (keys.length === 1) throw new TypeError("Failed to fetch"); // response lost
      return jsonResponse(201, { id: 7 });
    });
    const out = await api<{ id: number }>("/work-items", { method: "POST", body: { title: "x" }, idempotencyKey: "ui-abc-123" });
    expect(out.id).toBe(7);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(keys).toEqual(["ui-abc-123", "ui-abc-123"]);
  });

  it("never auto-retries a request without an idempotency key", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api("/work-items/1", { method: "PATCH", body: { version: 1 } })).rejects.toMatchObject({
      status: 0,
      code: "network_error",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("maps the server error envelope to ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(409, {
        error: { code: "version_conflict", message: "changed", details: { current_version: 3 } },
        request_id: "r1",
      }),
    );
    const err = (await api("/work-items/1").catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(409);
    expect(err.code).toBe("version_conflict");
    expect(err.details.current_version).toBe(3);
    expect(err.requestId).toBe("r1");
  });

  it("sends the CSRF header and extracts field errors", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(422, {
        error: {
          code: "validation_failed",
          message: "Request validation failed",
          details: { fields: [{ loc: ["body", "title"], message: "Value error, title must not be blank" }] },
        },
      }),
    );
    const err = (await api("/work-items", { method: "POST", body: {} }).catch((e) => e)) as ApiError;
    expect(new Headers(spy.mock.calls[0][1]?.headers).get("X-Requested-With")).toBe("opsflow");
    expect(err.fieldErrors()).toEqual({ title: "title must not be blank" });
  });
});
