console.log("JavaScript file loaded");

// Load system types from JSON file and populate the dropdown
fetch('/static/json/system_types.json')
    .then(response => response.json())
    .then(data => {
        const systemTypeSelects = document.querySelectorAll('.system_type');
        systemTypeSelects.forEach(select => {
            data.system_types.forEach(type => {
                const option = document.createElement('option');
                option.value = type;
                option.textContent = type;
                select.appendChild(option);
            });
        });
    })
    .catch(error => console.error('Error loading system types:', error));

// Load building types from JSON file and populate the dropdown
fetch('/static/json/building_types.json')
    .then(response => response.json())
    .then(data => {
        const buildingTypeSelects = document.querySelectorAll('.building_type');
        buildingTypeSelects.forEach(select => {
            data.building_types.forEach(type => {
                const option = document.createElement('option');
                option.value = type;
                option.textContent = type;
                select.appendChild(option);
            });
        });
    })
    .catch(error => console.error('Error loading building types:', error));

// Load building types from JSON file and populate the dropdown
fetch('/static/json/locations.json')
    .then(response => response.json())
    .then(data => {
        const locationsSelects = document.querySelectorAll('.locations');
        locationsSelects.forEach(select => {
            data.locations.forEach(type => {
                const option = document.createElement('option');
                option.value = type;
                option.textContent = type;
                select.appendChild(option);
            });
        });
    })
    .catch(error => console.error('Error loading locations:', error));

function showTab(tabId) {
    console.log(`Showing tab: ${tabId}`);
    document.querySelectorAll('.tab').forEach(tab => {
        tab.classList.remove('active');
    });
    document.getElementById(tabId).classList.add('active');
}

function toggleConfigurationSettings() {
    var advancedSettings = document.getElementById('configuration-settings');
    var checkbox = document.getElementById('show-configuration-settings');
    if (checkbox.checked) {
        advancedSettings.style.display = 'block';
    } else {
        advancedSettings.style.display = 'none';
    }
}


////////////////////////////////////////////////////////////////////////////////////////
//////////////////////////////// API CALLS /////////////////////////////////////////////
////////////////////////////////////////////////////////////////////////////////////////

//////////////////////////////// Dev CALLS /////////////////////////////////////////////

async function generateFeatureFiles() {
    console.log("generateFeatureFiles script loaded");

    const asset_geojson_file = document.getElementById('asset_geojson_file').files[0];
    const metadata_csv_file = document.getElementById('metadata_csv_file_1').files[0];
    const num_cores = document.getElementById('num_cores').value;

    if (!(asset_geojson_file && metadata_csv_file)) {
        alert('Please upload both the GeoJSON and CSV files.');
        console.error("Please upload both the GeoJSON and CSV files.");
        return;
    }

    // Add form data for configuration properties
    const configData = {
        weekday_start_time: document.getElementById('weekday_start_time').value,
        weekday_duration: document.getElementById('weekday_duration').value,
        weekend_start_time: document.getElementById('weekend_start_time').value,
        weekend_duration: document.getElementById('weekend_duration').value,
        system_type: document.querySelector('.system_type').value,
        heating_system_fuel_type: document.getElementById('heating_system_fuel_type').value,
        constructions: {
            wall: {
                material: document.getElementById('wall_material').value,
                r_value: document.getElementById('wall_r_value').value
            },
            roof: {
                material: document.getElementById('roof_material').value,
                r_value: document.getElementById('roof_r_value').value
            }
        }
    };


    const formData = new FormData();
    
    formData.append('asset_geojson_file', asset_geojson_file);
    formData.append('metadata_csv_file_1', metadata_csv_file);
    formData.append('config_data', JSON.stringify(configData));
    formData.append('num_cores', num_cores);


    try {
        console.log("Sending POST request to /api/featurefiles");
        const response = await fetch('/api/featurefiles', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'feature_files.zip';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Error:', error);
    }
}

async function startUOSimulation() {
    console.log("startUOSimulation script loaded"); 
    
    const asset_id = document.getElementById('asset_id_2').value;
    const feature_file = document.getElementById('featurefile_zip').files[0];
    const clean_report = document.getElementById('clean_report_1').checked;


    // Ensure that either feature_file or asset_id is provided, but not both
    if ((feature_file && asset_id) || (!feature_file && !asset_id)) {
        alert('Please upload either a feature file zip or input an asset id, but not both.');
        console.error("Please upload either a feature file zip or input an asset id, but not both.");
        return;
    }

    // Ensure the feature file is a zip file
    if (feature_file && !feature_file.name.endsWith('.zip')) {
        alert('Please upload a valid zip file.');
        console.error("Feature file is not a zip file");
        return;
    }

    // Ensure the asset id is a number
    if (asset_id && isNaN(asset_id)) {
        alert('Please enter a valid numeric asset id.');
        console.error("Asset ID is not a number");
        return;
    }


    let formData = new FormData();

    formData.append('asset_id_2', asset_id);
    formData.append('featurefile_zip', feature_file);
    formData.append('clean_report_1', clean_report);

    
    try {
        console.log("Sending POST request to /api/UOsimulation/start");
        const response = await fetch('/api/UOsimulation/start', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const data = await response.json();
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
    }
}

async function getCleanReport() {
    console.log("getCleanReport script loaded");
    // The data should be stored in the databased and also all zipped into a folder
    // for the user to download
    const unclean_report_csv = document.getElementById('unclean_report_csv').files[0];
    const metadata_csv_file = document.getElementById('metadata_csv_file_2').files[0];
    const asset_id = document.getElementById('asset_id').value;

    // Ensure the unclean report is a csv file
    if (unclean_report_csv && !unclean_report_csv.name.endsWith('.csv')) {
        alert('Please upload a valid unclean report csv file.');
        console.error("Unclean Report is not a csv file");
        return;
    }

    // Ensure the metadata file is a csv file
    if (metadata_csv_file && !metadata_csv_file.name.endsWith('.csv')) {
        alert('Please upload a valid metadata csv file.');
        console.error("Metadata is not a csv file");
        return;
    }

    // Ensure the asset id is a number
    if (asset_id && isNaN(asset_id)) {
        alert('Please enter a valid numeric asset id.');
        console.error("Asset ID is not a number");
        return;
    }

    let formData = new FormData();

    formData.append('asset_id', asset_id);
    formData.append('unclean_report_csv', unclean_report_csv);
    formData.append('metadata_csv_file_2', metadata_csv_file);

    try {
        console.log("Sending POST request to /api/clean_report");
        const response = await fetch('/api/clean_report', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            console.error("Response not OK");
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }
        const data = await response.json();
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
    }

}

async function assetAnalysis() {
    console.log("assetAnalysis script loaded");
    const feature_file = document.getElementById('featurefile_zip').files[0];


    // Ensure the feature file is a zip file
    if (feature_file && !feature_file.name.endsWith('.zip')) {
        alert('Please upload a valid zip file.');
        console.error("Feature file is not a zip file");
        return;
    }


    let formData = new FormData();

    formData.append('featurefile_zip', feature_file);

    
    try {
        console.log("Sending POST request to /api/diagnostics/analysis");
        const response = await fetch('/api/diagnostics/analysis', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const data = await response.json();
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
    }
}


//////////////////////////////// Simulation CALLS /////////////////////////////////////////////
function autorunSimulation() {
    fetch('/autorun_simulation', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        }
    })
    .then(response => response.json())
    .then(data => {
        alert(data.message);
    })
    .catch(error => {
        console.error('Error:', error);
    });
}

async function startSimulation() {
    console.log("startSimulation script loaded");

    const asset_geojson_file = document.getElementById('startsim_asset_geojson_file').files[0];
    const metadata_csv_file = document.getElementById('startsim_metadata_csv_file_1').files[0];
    const simulation_name = document.getElementById('startsim_simulation_name').value;
    const num_cores = document.getElementById('startsim_num_cores').value;
    const locations = document.querySelector('.locations').value;

    if (!(asset_geojson_file && metadata_csv_file)) {
        alert('Please upload both the GeoJSON and CSV files.');
        console.error("Please upload both the GeoJSON and CSV files.");
        return;
    }

    if (!simulation_name) {
        alert('Please enter a simulation name.');
        console.error("Please enter a simulation name.");
        return;
    }
    
    // Add form data for configuration properties
    const configData = {
        weekday_start_time: document.getElementById('weekday_start_time').value,
        weekday_duration: document.getElementById('weekday_duration').value,
        weekend_start_time: document.getElementById('weekend_start_time').value,
        weekend_duration: document.getElementById('weekend_duration').value,
        system_type: document.querySelector('.system_type').value,
        heating_system_fuel_type: document.getElementById('heating_system_fuel_type').value,
        constructions: {
            wall: {
                material: document.getElementById('wall_material').value,
                r_value: document.getElementById('wall_r_value').value
            },
            roof: {
                material: document.getElementById('roof_material').value,
                r_value: document.getElementById('roof_r_value').value
            }
        }
    };


    const formData = new FormData();
    
    formData.append('simulation_name', simulation_name);
    formData.append('asset_geojson_file', asset_geojson_file);
    formData.append('metadata_csv_file', metadata_csv_file);
    formData.append('config_data', JSON.stringify(configData));
    formData.append('location', locations);
    formData.append('num_cores', num_cores);


    try {
        console.log("Sending POST request to /api/simulation/start");
        const response = await fetch('/api/simulation/start', {
            method: 'POST',
            body: formData
        });
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
    } catch (error) {
        console.error('Error:', error);
    }
}

async function getSimulationStatus() {
    const simulation_name = document.getElementById('status_simulation_name').value;
    const batchId = document.getElementById('status_batch_id').value;

    if (!simulation_name) {
        alert('Please enter a simulation name.');
        console.error("Simulation name is required");
        return;
    }

    let url = `/api/simulation/status/${simulation_name}`;
    if (batchId) {
        url += `?batch_id=${batchId}`;
    }

    try {
        console.log(`Sending GET request to ${url}`);
        const response = await fetch(url);
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const data = await response.json();
        console.log(data);
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while fetching the simulation status.');
    }
}

async function stopSimulation() {
    const url = '/api/simulation/stop'; // Ensure this matches the route in app.py

    try {
        console.log(`Sending POST request to ${url}`);
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const data = await response.json();
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while stopping the simulation.');
    }
}
//////////////////////////////// Mangement CALLS /////////////////////////////////////////////

async function getAssetConfig() {
    console.log("getAssetConfig script loaded");
    

    const asset_id = document.getElementById('get_asset_id_config').value;
    const simulation_name = document.getElementById('get_simulation_name_config').value;

    // Ensure the asset id is a number
    if (asset_id && isNaN(asset_id)) {
        alert('Please enter a valid numeric asset id.');
        console.error("Asset ID is not a number");
        return;
    }

    if (!simulation_name || !asset_id) {
        alert('Please enter a simulation name and an asset id.');
        console.error("Simulation name and asset id are required");
    }

    try {
        console.log("Sending GET request to /api/asset/config");
        const response = await fetch(`/api/asset/config/${simulation_name}/${asset_id}`);
        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = `${asset_id}_config.json`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while fetching the asset configuration.');
    }
}

//////////////////////////////// Diagnostic CALLS /////////////////////////////////////////////
async function recoverSimulation() {
    const corrupted_simulation_name = document.getElementById('corrupted_simulation_name').value;
    const recover_simulation_name = document.getElementById('recover_simulation_name').value;
    const batch_id = document.getElementById('recover_batch_id').value;
    const num_cores = document.getElementById('recover_num_cores').value;

    if (!corrupted_simulation_name || !recover_simulation_name) {
        alert('Please completed all the fields');
        console.error("Please completed all the fields");
        return;
    }

    let formData = new FormData();
    formData.append('corrupted_simulation_name', corrupted_simulation_name);
    formData.append('recover_simulation_name', recover_simulation_name);
    formData.append('recover_num_cores', num_cores);

    if (batch_id) {
        formData.append('recover_batch_id', batch_id);
    }

    try {
        console.log("Sending POST request to /api/diagnostics/recovery");
        const response = await fetch('/api/diagnostics/recovery', {
            method: 'POST',
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            console.error("Response not OK");
            return;
        }

        const data = await response.json();
        alert(`Recovery process completed: ${data.message}`);
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred during the recovery process.');
    }
}

async function getLogs() {
    console.log("getLogs script loaded");

    try {
        console.log("Sending GET request to /api/diagnostics/getlogs");
        const response = await fetch('/api/diagnostics/getlogs', {
            method: 'GET'
        });
        if (!response.ok) {
            console.error("Response not OK");
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.style.display = 'none';
        a.href = url;
        a.download = 'logs.zip';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Error:', error);
    }
}

async function logMessage(message, type = 'log') {
    
    try {
        await fetch('/api/diagnostics/log', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message, type })
        });
    
    } catch (error) {
        console.error('Error logging message:', error);
    }
}

console.log = function(message) {
    logMessage(message, 'log');
};

console.error = function(message) {
    logMessage(message, 'error');
};