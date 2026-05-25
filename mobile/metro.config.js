const { getDefaultConfig } = require("expo/metro-config");

const config = getDefaultConfig(__dirname);

config.resolver.nodeModulesPaths = [`${__dirname}/node_modules`];
config.watchFolders = [__dirname];

module.exports = config;
