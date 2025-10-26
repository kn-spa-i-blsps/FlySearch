# flysearch

Procedure to send the photo from RPi to your laptop

1. Make sure Docker is running 
2. Create an .env file according to .env_example, add it to your /docker directory on your laptop and RPi
3. On your laptop, build the image:   
```docker build -t flysearch:latest .```
4. On your laptop, run docker-compose:   
```docker compose --profile server up --build```
5. On your laptop, in new terminal tab, run:   
```cloudflared tunnel --url http://localhost:PORT/``` (substitute your port)
6. Copy the generated link of type: https://your-server.trycloudflare.com and substitute https to wss to get 
wss://your-server.trycloudflare.com   
7. On your RPi, build the image:   
```docker build -t flysearch:latest .```
8. On your RPi, run docker-compose with a SERVER_URL variable: 
```SERVER_URL=wss://your-server.trycloudflare.com docker compose --profile producer up --build```
9. Search for the photo on your laptop, in the project root, in the directory /uploads
10. To stop the connection gracefully, click ctrl+C on your laptop, in the tab where you ran docker-compose                             