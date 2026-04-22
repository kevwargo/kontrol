/*
  injected from python:
  RULES: [Rule];
  DBUS_NAME: string;
*/

function kwinctlRegister(id, rule) {
  print(`kwinctl: binding ${rule.key} to ${id}`);
  registerShortcut(`kwinctl_${id}`, `Focus ${id} (KWinCTL)`, rule.key, () => {
    const found = workspace
      .windowList()
      .find((w) => w.resourceClass === rule.cls);
    if (found) {
      print(
        `kwinctl: found ${id}, switching from ${workspace.activeWindow} to ${found}`,
      );
      workspace.activeWindow = found;
    } else if (rule.command) {
      if (rule.auto) {
        print(`kwinctl: ${id} not found, calling command ${rule.command}`);
        callDBus(DBUS_NAME, "/", DBUS_NAME, "Execute", rule.command);
      } else {
        print(`kwinctl: ${id} not found, querying ${rule.command}`);
        callDBus("org.kde.krunner", "/App", "org.kde.krunner.App", "display");
        callDBus(
          "org.kde.krunner",
          "/App",
          "org.kde.krunner.App",
          "query",
          rule.command,
        );
      }
    }
  });
}

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

function onNewWindow(window) {
  if (!window.normalWindow) return;

  const rule = RULES.find((r) => r.cls === window.resourceClass);
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
const wsfmt = (ws) => `[${ws.map(wfmt).join("; ")}]`;

RULES.forEach((r) =>
  registerShortcut(`kwinctl_${r.id}`, `Focus ${r.id} (KWinCTL)`, r.key, () =>
    triggerRule(r),
  ),
);

workspace.windowList().forEach(onNewWindow);

workspace.windowAdded.connect(onNewWindow);
workspace.windowActivated.connect(onWindowActivate);
workspace.windowRemoved.connect(onWindowRemove);
