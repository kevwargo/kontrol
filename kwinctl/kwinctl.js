function registerAction(act) {
  print(`NAMESPACE ${namespace}: registering action ${act.id} to ${act.key}`);
  registerShortcut(act.id, act.key, act.key, () => {
    print(`NAMESPACE ${namespace}: action ${act.id} triggered by ${act.key}`);
    callDBus(
      dbusName,
      "/",
      dbusName,
      "Execute",
      `NAMESPACE ${namespace}: action ${act.id} triggered by ${act.key}`,
    );
  });
}

for (act of actions) {
  registerAction(act);
}
