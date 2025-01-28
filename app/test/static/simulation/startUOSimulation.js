async function startUOSimulation() {
    console.log("startUOSimulation script loaded"); 
    const asset_id = document.getElementById('asset_id').value;
    const feature_file = document.getElementById('featurefile_zip').files[0];

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

    const form = document.getElementById('start-uosimulation-form');
    let formData = new FormData(form);
    
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

module.exports = startUOSimulation;