exports.config = {
  runner: 'local',
  specs: [
    './specs/*.js'
  ],
  exclude: [],
  maxInstances: 1,
  capabilities: [{
    maxInstances: 1,
    browserName: 'chrome'
  }],
  services: ['chromedriver'],
  sync: true,
  logLevel: 'info',
  coloredLogs: true,
  bail: 0,
  baseUrl: 'http://localhost',
  waitforTimeout: 70000,
  connectionRetryTimeout: 90000,
  connectionRetryCount: 3,
  deprecationWarnings: false,
  framework: 'jasmine',
  reporters: ['spec'],
  jasmineNodeOpts: {
    defaultTimeoutInterval: 60000
  }
};
