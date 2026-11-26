// Owned by spec-harness.
const dispatcher = __SPEC_DISPATCHER__;
const executable = __SPEC_EXECUTABLE__;

export default function specHook(pi: any) {
  const call = async (event: string, ctx: any, payload = {}) => {
    const result = await pi.exec(executable, [dispatcher, "--host", "pi", event], {
      input: JSON.stringify({ ...payload, cwd: ctx.cwd, session_id: ctx.sessionManager.getSessionId() }),
    });
    return result.code === 0 || result.code === undefined ? JSON.parse(result.stdout || "{}") : {};
  };
  const message = (content: string) => ({ customType: "spec-harness", content, display: false });
  pi.on("session_start", async (event: any, ctx: any) => {
    const result = await call("start", ctx, { source: event.reason });
    if (result.context) pi.sendMessage(message(result.context), { deliverAs: "nextTurn" });
  });
  pi.on("before_agent_start", async (event: any, ctx: any) => {
    if (event.messageType !== "user") return;
    const result = await call("prompt", ctx);
    if (result.context) return { message: message(result.context) };
  });
  pi.on("tool_call", async (event: any, ctx: any) => {
    if (event.toolName !== "bash") return;
    const result = await call("pre_tool", ctx, { command: event.input.command });
    if (result.decision === "deny") return { block: true, reason: result.reason };
  });
  pi.on("agent_before_settle", async (event: any, ctx: any) => {
    if (event.outcome !== "completed" || !event.context.canContinue || event.continue) return;
    const result = await call("stop", ctx);
    if (result.decision === "block" && result.reason) {
      return { continue: true, entries: [...event.entries, { type: "custom_message", ...message(result.reason) }] };
    }
    if (result.context && ctx.hasUI) ctx.ui.notify(result.context, "info");
  });
}
