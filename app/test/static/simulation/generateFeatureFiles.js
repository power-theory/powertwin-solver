/************************************************************************************/
// async function generateFeatureFiles()
// This async function generates feature files from the uploaded GeoJSON and CSV files.
// The function sends a POST request to the server with the uploaded files.
// The server processes the files and returns a ZIP file containing the feature files.
// The function is called when the "Generate Feature Files" button is clicked.
/************************************************************************************/
async function generateFeatureFiles() {
    console.log("generateFeatureFiles script loaded");

    const assetGeojsonFile = document.getElementById('asset_geojson_file').files[0];
    const metadataCsvFile = document.getElementById('metadata_csv_file').files[0];

    if (!(assetGeojsonFile && metadataCsvFile)) {
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
    console.log('Configuration data:', configData);

    const formData = new FormData();
    
    formData.append('asset_geojson_file', assetGeojsonFile);
    formData.append('metadata_csv_file', metadataCsvFile);
    formData.append('config_data', JSON.stringify(configData));


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

module.exports = generateFeatureFiles;