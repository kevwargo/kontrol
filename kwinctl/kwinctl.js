/*
  injected above from python:
  DBUS_NAME: string;
  RULES: Rule[];
  COMMANDS: {string: Command};
*/

const rulesByWindowId = {};

function wfmt(w) {
  return `${w.resourceName}(${w.caption})${w.internalId}`;
}

function wsfmt(ws) {
  return `[${(ws ?? []).map(wfmt).join("; ")}]`;
}

function triggerRule({ id, key, candidates, command, auto }) {
  const log = (msg) => print(`kwinctl rule ${id}: ${msg}`);

  log(
    `triggered by ${key}; active=${wfmt(workspace.activeWindow)} candidates=${wsfmt(candidates)}`,
  );

  if (candidates?.length) {
    let candidate = candidates[0];
    if (workspace.activeWindow === candidate && candidates.length) {
      candidates.push(candidates.shift());
      candidate = candidates[0];
      log(`rearranged candidates: ${wsfmt(candidates)}`);
    }
    log(`activating ${wfmt(candidate)}`);
    workspace.activeWindow = candidate;
  } else if (command) {
    if (auto) {
      log(`not found, executing ${command}`);
      selfDBus("RunShellCommand", command);
    } else {
      log(`not found, prompting ${command}`);
      krunnerPrompt(command);
    }
  } else {
    log("not found, ignoring");
  }
}

function triggerCommand({ id, cmd }) {
  print(`kwinctl cmd ${id} triggered by ${cmd.key}`);

  selfDBus("RunCommand", id);
}

function selfDBus(method, ...args) {
  callDBus(DBUS_NAME, "/", DBUS_NAME, method, ...args);
}

function krunnerPrompt(cmd) {
  callDBus("org.kde.krunner", "/App", "org.kde.krunner.App", "display");
  callDBus("org.kde.krunner", "/App", "org.kde.krunner.App", "query", cmd);
}

function matchRule(rule, window) {
  if (!rule.cls && !rule.caption) {
    print(`kwinctl matcher: ignoring rule with empty matching props: ${rule}`);
    return false;
  }

  if (rule.cls && rule.cls !== window.resourceClass) return false;
  if (rule.caption && rule.caption !== window.caption) return false;

  print(`kwinctl matcher: ${wfmt(window)} matched by ${rule}`);

  return true;
}

function onNewWindow(window) {
  if (!window.normalWindow) return;

  const rule = RULES.find((r) => matchRule(r, window));
  if (!rule) {
    print(`${wfmt(window)} is not matched by any rule, ignoring it`);
    return;
  }

  rulesByWindowId[window.internalId] = rule;
  rule.candidates = [window, ...(rule.candidates ?? [])];

  print(
    `kwinctl rule ${rule.id}: added ${wfmt(window)} to ${wsfmt(rule.candidates)}`,
  );
}

function onWindowRemove(window) {
  const rule = rulesByWindowId[window.internalId];
  if (!rule) return;

  rule.candidates = rule.candidates.filter((w) => w !== window);
  print(
    `kwinctl rule ${rule.id}: removed ${wfmt(window)} from ${wsfmt(rule.candidates)}`,
  );

  delete rulesByWindowId[window.internalId];
}

function onWindowActivate(window) {}

RULES.forEach((r) => {
  print(`kwinctl: binding ${r.key} to rule ${JSON.stringify(r)}`);
  registerShortcut(
    `kwinctl_rule_${r.id}`,
    `KWinCTL: Focus ${r.id}`,
    r.key,
    () => triggerRule(r),
  );
});

Object.entries(COMMANDS).forEach(([id, cmd]) => {
  print(`kwinctl: binding ${cmd.key} to command ${JSON.stringify(cmd)}`);
  registerShortcut(`kwinctl_cmd_${id}`, `KWinCTL: Run ${id}`, cmd.key, () =>
    triggerCommand({ id, cmd }),
  );
});

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
