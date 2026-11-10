// Owned by spec-harness.
export default function register(api: any) {
  const forward = async (event: string, payload: any) => {
    const result = await api.exec("python3", [new URL("./spec_hook.py", import.meta.url).pathname, "--host", "openclaw", event], { input: JSON.stringify(payload) });
    return result.stdout ? JSON.parse(result.stdout) : {};
  };
  for (const event of ["start", "prompt", "pre_tool", "pre_compact", "stop", "end"]) api.on(event, (payload: any) => forward(event, payload));
}
