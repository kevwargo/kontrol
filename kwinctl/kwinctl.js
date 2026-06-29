/*
  injected above from python:
  DBUS_NAME: string;
  RULES: Rule[];
  COMMANDS: Command[];
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

function triggerCommand({ id, key, shell, cmd, prompt }) {
  print(`kwinctl cmd ${id} triggered by ${key}`);

  if (shell) {
    print(`running shell ${shell}`);
    selfDBus("RunShellCommand", shell);
  } else if (cmd) {
    print(`running raw ${cmd}`);
    selfDBus("RunCommand", cmd);
  } else if (prompt) {
    print(`showing prompt ${prompt}`);
    krunnerPrompt(prompt);
  }
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

  print(`kwinctl matcher: ${wfmt(window)} matched by ${JSON.stringify(rule)}`);

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

COMMANDS.forEach((c) => {
  print(`kwinctl: binding ${c.key} to command ${JSON.stringify(c)}`);
  registerShortcut(`kwinctl_cmd_${c.id}`, `KWinCTL: Run ${c.id}`, c.key, () =>
    triggerCommand(c),
  );
});

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
