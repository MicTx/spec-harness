// Owned by spec-harness.
const dispatcher = __SPEC_DISPATCHER__;
const executable = __SPEC_EXECUTABLE__;

export default async function specHook(input: { event: string; [key: string]: unknown }) {
  const { spawnSync } = await import("node:child_process");
  const result = spawnSync(executable, [dispatcher, "--host", "opencode", input.event], { input: JSON.stringify(input), encoding: "utf8" });
  return result.stdout ? JSON.parse(result.stdout) : {};
}
