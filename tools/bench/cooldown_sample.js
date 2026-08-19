// Single-connection cooldown sampler for the ticket-pool decay experiment
// (GitHub issue #3). Runs entirely inside one mongosh session so there is
// exactly one client connection open for the whole cooldown window -- no
// pooled-socket heartbeat traffic to confound "zero load" (see plan
// docs/plans/issue-3-ticket-pool-convergence-and-decay.md, guard 4c/5).
//
// Prints one JSON line per sample to stdout. Stops early if readTotal has
// held at the resting floor (4) for 60 continuous seconds; otherwise runs
// for COOLDOWN_SECONDS.

const COOLDOWN_SECONDS = 900;
const SAMPLE_S = 5;
const FLOOR = 4;
const FLOOR_HOLD_S = 60;

const t0 = new Date().getTime();
let floorSinceMs = null;

while (true) {
  const now = new Date().getTime();
  const sinceStart = (now - t0) / 1000;
  if (sinceStart > COOLDOWN_SECONDS) {
    print("COOLDOWN_END: reached " + COOLDOWN_SECONDS + "s without a sustained floor hold");
    break;
  }
  const s = db.adminCommand({serverStatus: 1});
  const c = s.wiredTiger.concurrentTransactions;
  const g = s.globalLock;
  const readTotal = c.read.totalTickets;
  const readOut = c.read.out;
  const queueLength = c.read.queueLength === undefined ? 0 : c.read.queueLength;

  print(JSON.stringify({
    t: now / 1000,
    sinceLoadStoppedSeconds: Math.round(sinceStart * 10) / 10,
    readTotal: readTotal,
    readOut: readOut,
    queueLength: queueLength,
    currentQueueReaders: g.currentQueue.readers,
    activeReaders: g.activeClients.readers,
  }));

  if (readTotal <= FLOOR) {
    if (floorSinceMs === null) {
      floorSinceMs = now;
    } else if ((now - floorSinceMs) / 1000 >= FLOOR_HOLD_S) {
      print("COOLDOWN_END: readTotal held at floor(" + FLOOR + ") for " + FLOOR_HOLD_S + "s, at sinceLoadStoppedSeconds=" + Math.round(sinceStart));
      break;
    }
  } else {
    floorSinceMs = null;
  }

  sleep(SAMPLE_S * 1000);
}
