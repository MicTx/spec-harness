// Owned by spec-harness.
const dispatcher = __SPEC_DISPATCHER__;
const executable = __SPEC_EXECUTABLE__;

export default function register(api: any) {
  const forward = async (event: string, payload: any) => {
    const result = await api.exec(executable, [dispatcher, "--host", "openclaw", event], { input: JSON.stringify(payload) });
    return result.stdout ? JSON.parse(result.stdout) : {};
  };
  for (const event of ["start", "prompt", "pre_tool", "stop"]) api.on(event, (payload: any) => forward(event, payload));
}
