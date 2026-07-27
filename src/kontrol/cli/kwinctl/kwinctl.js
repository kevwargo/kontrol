/*
  injected above from python:
  DBUS_NAME: string;
  RULES: Rule[];
  COMMANDS: {string: Command};
*/

const rulesByWindowId = {};

const builtinCommands = {
  activeWindowToLeftEdge() {
    const w = workspace.activeWindow;
    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      x: w.output.geometry.x,
    });
  },
  activeWindowToRightEdge() {
    const w = workspace.activeWindow;
    const sg = w.output.geometry;
    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      x: sg.x + sg.width - w.width,
    });
  },
  activeWindowToTopEdge() {
    const w = workspace.activeWindow;
    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      y: w.output.geometry.y,
    });
  },
  activeWindowToBottomEdge() {
    const aw = workspace.activeWindow;
    const sg = aw.output.geometry;
    const panel = workspace
      .windowList()
      .find((w) => w.dock && w.output == aw.output);
    const panelHeight = panel ? panel.height : 0;

    aw.frameGeometry = Object.assign({}, aw.frameGeometry, {
      y: sg.y + sg.height - panelHeight - aw.height,
    });
  },
};

function findPanelForWindow(window) {}

function log(msg) {
  console.info(`KWinCTL: ${msg}`);
}

function wfmt(w) {
  return `${w.resourceName}(${w.caption})${w.internalId}`;
}

function wsfmt(ws) {
  return `[${(ws ?? []).map(wfmt).join("; ")}]`;
}

function triggerRule({ id, key, candidates, command, auto }) {
  const logrule = (msg) => log(`rule ${id}: ${msg}`);

  logrule(
    `triggered by ${key}; active=${wfmt(workspace.activeWindow)} candidates=${wsfmt(candidates)}`,
  );

  if (candidates?.length) {
    let candidate = candidates[0];
    if (workspace.activeWindow === candidate && candidates.length) {
      candidates.push(candidates.shift());
      candidate = candidates[0];
      logrule(`rearranged candidates: ${wsfmt(candidates)}`);
    }
    logrule(`activating ${wfmt(candidate)}`);
    workspace.activeWindow = candidate;
  } else if (command) {
    if (auto) {
      logrule(`not found, executing ${command}`);
      selfDBus("RunShellCommand", command);
    } else {
      logrule(`not found, prompting ${command}`);
      krunnerPrompt(command);
    }
  } else {
    logrule("not found, ignoring");
  }
}

function triggerCommand({ id, cmd }) {
  log(`cmd ${id} triggered by ${cmd.key}`);

  const { builtin } = cmd;
  if (builtin) {
    builtinCmd = builtinCommands[builtin];
    if (builtinCmd) {
      builtinCmd();
    } else {
      console.warning(`Built-in command ${builtin} not found`);
    }
  }

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
    log(`matcher: ignoring rule with empty matching props: ${rule}`);
    return false;
  }

  if (rule.cls && rule.cls !== window.resourceClass) return false;
  if (rule.caption && rule.caption !== window.caption) return false;

  log(`matcher: ${wfmt(window)} matched by ${rule}`);

  return true;
}

function onNewWindow(window) {
  if (!window.normalWindow) return;

  const rule = RULES.find((r) => matchRule(r, window));
  if (!rule) {
    log(`${wfmt(window)} is not matched by any rule, ignoring it`);
    return;
  }

  rulesByWindowId[window.internalId] = rule;
  rule.candidates = [window, ...(rule.candidates ?? [])];

  log(`rule ${rule.id}: added ${wfmt(window)} to ${wsfmt(rule.candidates)}`);
}

function onWindowRemove(window) {
  const rule = rulesByWindowId[window.internalId];
  if (!rule) return;

  rule.candidates = rule.candidates.filter((w) => w !== window);
  log(
    `rule ${rule.id}: removed ${wfmt(window)} from ${wsfmt(rule.candidates)}`,
  );

  delete rulesByWindowId[window.internalId];
}

function onWindowActivate(window) {}

RULES.forEach((r) => {
  log(`binding ${r.key} to rule ${JSON.stringify(r)}`);
  registerShortcut(
    `kwinctl_rule_${r.id}`,
    `KWinCTL: Focus ${r.id}`,
    r.key,
    () => triggerRule(r),
  );
});

Object.entries(COMMANDS).forEach(([id, cmd]) => {
  log(`binding ${cmd.key} to command ${JSON.stringify(cmd)}`);
  registerShortcut(`kwinctl_cmd_${id}`, `KWinCTL: Run ${id}`, cmd.key, () =>
    triggerCommand({ id, cmd }),
  );
});

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
