/*
  injected from python:
  RULES: [Rule];
  DBUS_NAME: string;
*/

const rulesByWindowId = {};

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
      callDBus(DBUS_NAME, "/", DBUS_NAME, "Execute", command);
    } else {
      log(`not found, querying ${command}`);
      callDBus("org.kde.krunner", "/App", "org.kde.krunner.App", "display");
      callDBus(
        "org.kde.krunner",
        "/App",
        "org.kde.krunner.App",
        "query",
        command,
      );
    }
  } else {
    log("not found, ignoring");
  }
}

function matchRule(rule, window) {
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

const wfmt = (w) => `${w.resourceName}(${w.caption})${w.internalId}`;
const wsfmt = (ws) => `[${(ws ?? []).map(wfmt).join("; ")}]`;

RULES.forEach((r) => {
  print(`kwinctl: binding ${r.key} to ${JSON.stringify(r)}`);
  registerShortcut(`kwinctl_${r.id}`, `Focus ${r.id} (KWinCTL)`, r.key, () =>
    triggerRule(r),
  );
});

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
