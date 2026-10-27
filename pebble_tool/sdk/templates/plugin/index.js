// Runs on the phone. Apps read the counter with Pebble.subscribeToSource and bump it with
// Pebble.invokeAction; both arrive here.

var COUNT_KEY = 'count';

function count() {
  return Number(localStorage.getItem(COUNT_KEY) || 0);
}

Pebble.registerSourceHandler(function (request, respond) {
  respond.data({
    instances: [{
      instanceId: 'counter',
      properties: {
        name: { shortText: { text: 'Counter' } },
        count: { numericValue: { value: count() }, shortText: { text: String(count()) } },
      },
    }],
  });
});

Pebble.registerActionHandler(function (request, respond) {
  if (request.action !== 'increment') {
    respond.error('INVALID_REQUEST', 'Unknown action ' + request.action);
    return;
  }
  localStorage.setItem(COUNT_KEY, String(count() + 1));
  respond.ok({ text: 'Counted to ' + count(), refreshed: ['demo/counter'] });
});
