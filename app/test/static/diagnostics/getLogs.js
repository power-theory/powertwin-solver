async function getLogs() {
    console.log("getLogs script loaded");

    try {
        console.log("Sending GET request to /api/getlogs");
        const response = await fetch('/api/getlogs', {
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
        a.download = 'all_logs.txt';
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
    } catch (error) {
        console.error('Error:', error);
    }
}