const showTab = require('../static/showTab.js');
const generateFeatureFiles = require('../static/simulation/generateFeatureFiles.js');
const startUOSimulation = require('../static/simulation/startUOSimulation.js');
// const getSimulationStatus = require('./getSimulationStatus.js');
// const getModelConfig = require('./configuration/getModelConfig.js');
// const updateModelConfig = require('./configuration/updateModelConfig.js');
// const getLogs = require('./logs/getLogs.js');


module.exports = (window) => {
    window.showTab = showTab;

    // Simulation Management
    window.generateFeatureFiles = generateFeatureFiles;
    window.startUOSimulation = startUOSimulation;
    // window.getSimulationStatus = getSimulationStatus;

    // // Configuration Management
    // window.getModelConfig = getModelConfig;
    // window.updateModelConfig = updateModelConfig;

    // // Logs Management
    // window.getLogs = getLogs;
};

// Call the function to attach the functions to the window object
require('./index.js')(window);