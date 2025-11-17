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
        const locationsSelects = document.querySelectorAll('.location');
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

function toggleHpcSettings() {
    var hpcSettings = document.getElementById('hpc-settings');
    var checkbox = document.getElementById('startsim_hpc_mode');
    if (checkbox.checked) {
        hpcSettings.style.display = 'block';
    } else {
        hpcSettings.style.display = 'none';
    }
}


////////////////////////////////////////////////////////////////////////////////////////
//////////////////////////////// API CALLS /////////////////////////////////////////////
////////////////////////////////////////////////////////////////////////////////////////


//////////////////////////////// Simulation CALLS /////////////////////////////////////////////
function autorunSimulation() {
    fetch('/api/simulation/autorun_simulation', {
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
    const metadata_csv_file = document.getElementById('startsim_metadata_csv_file').files[0];
    const simulation_name = document.getElementById('startsim_simulation_name').value;
    const num_cores = document.getElementById('startsim_num_cores').value;
    const location = document.querySelector('.location').value;
    const hpc_mode = document.getElementById('startsim_hpc_mode').checked;
    const shared_storage = document.getElementById('startsim_shared_storage').value;

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
    
    // HPC mode validation
    if (hpc_mode && !shared_storage) {
        alert('Shared storage path is required when HPC mode is enabled.');
        console.error("Shared storage path is required when HPC mode is enabled.");
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
    formData.append('location', location);
    formData.append('num_cores', num_cores);
    formData.append('hpc_mode', hpc_mode);
    if (hpc_mode && shared_storage) {
        formData.append('shared_storage', shared_storage);
    }
    
    // Add keep_dirs parameter
    const keepDirs = document.getElementById('startsim_keep_dirs').checked;
    formData.append('keep_dirs', keepDirs);


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

async function deleteSimulation() {
    const simulation_name = document.getElementById('delete_simulation_name').value;
    
    if (!simulation_name) {
        alert('Please enter a simulation name.');
        console.error("Simulation name is required");
        return;
    }

    let url = `/api/simulation/delete/${simulation_name}`;

    try {
        console.log(`Sending DELETE request to ${url}`);
        const response = await fetch(url, {
            method: 'DELETE',
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
        console.log(data);
        alert(JSON.stringify(data));
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while deleting the simulation.');
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
    
    // Add keep_dirs parameter
    const keepDirs = document.getElementById('recover_keep_dirs').checked;
    formData.append('keep_dirs', keepDirs);

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
        console.log("Sending GET request to /logs");
        const response = await fetch('/logs', {
            method: 'GET'
        });
        if (!response.ok) {
            console.error("Response not OK");
            const errorData = await response.json();
            alert(`Error: ${errorData.error}`);
            return;
        }
        // Redirect to /logs
        window.location.href = '/logs';
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