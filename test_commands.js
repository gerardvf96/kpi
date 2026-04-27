// BROWSER CONSOLE TEST COMMANDS
// Copy and paste these into your browser console after verifying your email

// 1. Check if JWT cookie exists
console.log('JWT Cookie:', document.cookie.split('; ').find(row => row.startsWith('link_access_token=')));

// 2. Get the exact formList URL that's failing
// Replace {uid} with your actual asset snapshot UID
const uid = 'fv8h493hv5gb5H'; // REPLACE THIS
const formListUrl = `http://kf.kobo.local/api/v2/asset_snapshots/${uid}/formList?formID=${uid}`;
console.log('Testing URL:', formListUrl);

// 3. Test the formList endpoint with fetch - see full response
fetch(formListUrl, {
    method: 'GET',
    credentials: 'include', // This sends cookies
    headers: {
        'Accept': 'application/xml'
    }
})
.then(response => {
    console.log('Status:', response.status);
    console.log('Headers:', [...response.headers.entries()]);
    return response.text();
})
.then(text => {
    console.log('Response body:', text);
})
.catch(error => {
    console.error('Fetch error:', error);
});

// 4. Test with explicit token in query param (if cookie fails)
const token = document.cookie.split('; ').find(row => row.startsWith('link_access_token='))?.split('=')[1];
if (token) {
    const urlWithToken = `${formListUrl}&link_access_token=${token}`;
    console.log('Testing with query token:', urlWithToken);
    
    fetch(urlWithToken, {
        method: 'GET',
        headers: {
            'Accept': 'application/xml'
        }
    })
    .then(response => {
        console.log('With token - Status:', response.status);
        return response.text();
    })
    .then(text => {
        console.log('With token - Response:', text);
    })
    .catch(error => {
        console.error('With token - Error:', error);
    });
} else {
    console.log('No JWT token found in cookies!');
}

// 5. Check what Enketo is actually requesting (open Network tab first)
console.log('Open Network tab and look for requests to:', formListUrl);
console.log('Check if they include the Cookie header');
