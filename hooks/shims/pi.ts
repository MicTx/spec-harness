// Owned by spec-harness.
export default async function specHook(pi: any) {
  const forward = async (event: string, payload: any, ctx: any) => {
    const result = await pi.exec("python3", [new URL("./spec_hook.py", import.meta.url).pathname, "--host", "pi", event], { input: JSON.stringify({ ...payload, cwd: ctx.cwd }) });
    return result.stdout ? JSON.parse(result.stdout) : {};
  };
  for (const event of ["start", "prompt", "pre_tool", "post_edit", "pre_compact", "subagent", "stop", "end"]) {
    pi.on(event, (payload: any, ctx: any) => forward(event, payload, ctx));
  }
}
