/* Shared analysis-task runner — the single home of the boilerplate every
   analysis_*.js module repeats: stale-response generation tickets, the
   half-second progress timer, run-button locking, and error surfacing.

   The per-module copies had already drifted (nnt/peters clear() missed
   clearInterval, so a cleared run kept its timer alive until the request
   settled); migrating modules onto this runner removes that whole defect
   class. Remaining modules migrate in batches (review P1-11). */
(function () {
  'use strict';
  window.RFAnalysisTask = Object.freeze({
    /* create({ host, run, reset, progress?, execute }) -> { clear, submit, generation }

       - host:   result host element; progress text and error.message land here
       - run:    trigger button; disabled while a request is in flight
       - reset(): clear the result UI (called on every invalidation)
       - progress(elapsedSeconds) -> string (optional; omit for no timer)
       - execute(ticket): async work; render inside; throw Error to surface a
         message. Stale tickets (clear() ran meanwhile) must skip rendering:
         `if (ticket !== task.generation()) return;` */
    create(config) {
      const { host, run, reset, progress, execute } = config;
      let generation = 0, timer = null;
      const stop = () => { if (timer !== null) { clearInterval(timer); timer = null; } };
      const clear = () => {
        generation++;
        stop();           // 在途请求的进度计时器随失效一并停掉（历史漂移缺陷点）
        reset();
        run.disabled = false;
      };
      const submit = async () => {
        clear();
        const ticket = generation;
        try {
          run.disabled = true;
          const started = performance.now();
          if (progress) {
            const tick = () => {
              if (ticket === generation) host.textContent = progress((performance.now() - started) / 1000);
            };
            tick(); timer = setInterval(tick, 500);
          }
          await execute(ticket);
        } catch (error) {
          if (ticket === generation) host.textContent = error.message;
        } finally {
          stop();
          if (ticket === generation) run.disabled = false;
        }
      };
      return Object.freeze({ clear, submit, generation: () => generation });
    }
  });
})();
