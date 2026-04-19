function kwinctlRegister(id, rule) {
  print(`kwinctl: registering ${id} to ${rule.key}`);
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
        callDBus(kwinctlDBus, "/", kwinctlDBus, "Execute", rule.command);
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

Object.keys(kwinctlRules).forEach((id) =>
  kwinctlRegister(id, kwinctlRules[id]),
);
