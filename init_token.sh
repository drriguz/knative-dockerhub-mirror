export PASSWORD="i won't tell you the password!"
export TOKEN=$(curl -s -H "Content-Type: application/json" -X POST -d '{"username": "knativecn", "password": "${PASSWORD}"}' https://hub.docker.com/v2/users/login/ | jq -r .token)
