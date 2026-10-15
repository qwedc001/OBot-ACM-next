import test from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import {
  captureRouteCapability,
  consumeCapability,
  takeFragment,
} from "../src/security/capability.js";
import { defineRegistry, permitted } from "../src/plugins/registry-core.js";
import { bindingOptions } from "../src/plugins/board/form.js";

test("capabilities are removed from URL and read only on permitted pages", () => {
  const raw = randomBytes(32).toString("base64url");
  for (const pathname of ["/login", "/board/bind"]) {
    const calls = [];
    assert.equal(
      takeFragment(
        { pathname, hash: "#" + raw, search: "?view=1" },
        { replaceState: (...args) => calls.push(args) },
      ),
      raw,
    );
    assert.deepEqual(calls[0], [null, "", pathname + "?view=1"]);
  }
  assert.equal(
    takeFragment(
      { pathname: "/commands", hash: "#" + raw },
      { replaceState: () => assert.fail("Must not consume unrelated hash") },
    ),
    null,
  );
  const calls = [];
  assert.equal(
    takeFragment(
      { pathname: "/login", hash: "#bad", search: "" },
      { replaceState: (...args) => calls.push(args) },
    ),
    null,
  );
  assert.equal(calls.length, 1);
});

test("plugin registry enforces slugs, route namespaces, ordering and permissions", () => {
  const plugin = (slug, order, superuser = false) => ({
    default: {
      manifest: { slug, home: "/" + slug, order, superuser },
      routes: [{ path: "/" + slug, name: slug }],
    },
  });
  const registry = defineRegistry({
    a: plugin("board", 20),
    b: plugin("runtime", 30, true),
    c: plugin("commands", 10),
  });
  assert.deepEqual(
    registry.map((p) => p.manifest.slug),
    ["commands", "board", "runtime"],
  );
  assert.deepEqual(
    permitted(registry, false).map((p) => p.manifest.slug),
    ["commands", "board"],
  );
  assert.equal(permitted(registry, true).length, 3);
  assert.throws(
    () => defineRegistry({ a: plugin("board", 1), b: plugin("board", 2) }),
    /duplicate/,
  );
  const invalid = plugin("board", 1);
  invalid.default.routes[0].path = "/board-elsewhere";
  assert.throws(() => defineRegistry({ invalid }), /route/);
});

test("binding options validate integers, bounds and real calendar dates", () => {
  assert.deepEqual(bindingOptions("2, 3 3，7", "", true), {
    exclude_uid: [2, 3, 7],
    exclude_before: "1970-01-01",
    show_unrated: true,
  });
  for (const value of [
    "-1",
    "1.2",
    "1e3",
    "abc",
    Array.from({ length: 101 }, (_, i) => i).join(","),
  ])
    assert.throws(() => bindingOptions(value, "", true));
  assert.throws(() => bindingOptions("", "2026-02-30", true));
  assert.equal(bindingOptions("", "2024-02-29", false).show_unrated, false);
});

test("same-document capabilities are consumed only by their intended page", () => {
  const raw = randomBytes(32).toString("base64url");
  assert.equal(captureRouteCapability("/board/bind", "#" + raw), true);
  assert.equal(consumeCapability("/login"), null);
  assert.equal(consumeCapability("/board/bind"), raw);
  assert.equal(consumeCapability("/board/bind"), null);
  assert.equal(captureRouteCapability("/commands", "#" + raw), false);
});
