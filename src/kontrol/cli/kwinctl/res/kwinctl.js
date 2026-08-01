/*
  injected above from python:
  DBUS_NAME: string;
  RULES: Rule[];
  COMMANDS: {string: Command};
*/

const rulesByWindowId = {};

const MAX_MODE_VERT = 1;
const MAX_MODE_HOR = 2;

const builtinCommands = {
  activeWindowToLeftEdge() {
    const w = workspace.activeWindow;
    if (w.maximizeMode & MAX_MODE_HOR) return;

    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      x: w.output.geometry.x,
    });
  },
  activeWindowToRightEdge() {
    const w = workspace.activeWindow;
    const sg = w.output.geometry;
    if (w.maximizeMode & MAX_MODE_HOR) return;

    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      x: sg.x + sg.width - w.width,
    });
  },
  activeWindowToTopEdge() {
    const w = workspace.activeWindow;
    if (w.maximizeMode & MAX_MODE_VERT) return;

    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      y: w.output.geometry.y,
    });
  },
  activeWindowToBottomEdge() {
    const w = workspace.activeWindow;
    if (w.maximizeMode & MAX_MODE_VERT) return;

    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      y: w.output.geometry.y + usableScreenHeight(w.output) - w.height,
    });
  },
  centerActiveWindow() {
    const w = workspace.activeWindow;
    const sg = w.output.geometry;
    if (w.maximizeMode === (MAX_MODE_VERT | MAX_MODE_HOR)) return;

    w.frameGeometry = Object.assign({}, w.frameGeometry, {
      x: sg.x + Math.max(0, Math.floor(sg.width / 2) - Math.floor(w.width / 2)),
      y:
        sg.y +
        Math.max(
          0,
          Math.floor(usableScreenHeight(w.output) / 2) -
            Math.floor(w.height / 2),
        ),
    });
  },
};

function usableScreenHeight(screen) {
  return (
    screen.geometry.height -
    (workspace.windowList().find((w) => w.dock && w.output == screen)?.height ??
      0)
  );
}

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

  if (cmd.builtinId) {
    builtinCmd = builtinCommands[cmd.builtinId];
    if (builtinCmd) {
      builtinCmd();
    } else {
      console.warning(`Built-in command ${cmd.builtinId} not found`);
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
    r.description ?? `KWinCTL: Focus ${r.id}`,
    r.key,
    () => triggerRule(r),
  );
});

Object.entries(COMMANDS).forEach(([id, cmd]) => {
  log(`binding ${cmd.key} to command ${JSON.stringify(cmd)}`);
  registerShortcut(
    `kwinctl_cmd_${id}`,
    cmd.description ?? `KWinCTL: Run ${id}`,
    cmd.key,
    () => triggerCommand({ id, cmd }),
  );
});

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
