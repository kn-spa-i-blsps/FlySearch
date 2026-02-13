# flysearch

Procedure to send the photo from RPi to your laptop

1. Make sure Docker is running 
2. Create an .env file according to .env_example, add it to your /docker directory on your laptop and RPi
3. On your laptop, build the image:   
```docker build -t flysearch:latest .```
4. On your laptop, run docker-compose in interactive mode:   
```docker compose run --rm --service-ports server python3 -u /app/client_server.py```
5. On your laptop, in new terminal tab, run:   
```cloudflared tunnel --url http://localhost:PORT/``` (substitute your port)
6. Copy the generated link of type: https://your-server.trycloudflare.com and substitute https to wss to get 
wss://your-server.trycloudflare.com   
7. On your RPi, build the image:   
```docker build -t flysearch:latest .```
8. On your RPi, run docker-compose with a SERVER_URL variable: 
```SERVER_URL=wss://your-server.trycloudflare.com docker compose --profile producer up --build```  
If you run it without the connection with Pixhawk (for tests), run this instead:  
```SERVER_URL=wss://your-server.trycloudflare.com docker compose --profile producer_test up --build```
9. Search for the photo on your laptop, in the project root, in the directory /uploads
10. To stop the connection gracefully, click ctrl+C on your laptop, in the tab where you ran docker-compose

Procedure to pull the recorded videos from RPi to your server:  
1. The video recording will begin automatically when WS connection between RPi and your server is established, and will finish when the connection is stopped.  
2. The video will be automatically saved with a timestamp to /video folder with .h264 format.
3. To pull it to server, use (in the desired directory, else replace . with a correct location):
```rsync -avP "pi@<IP>:<FLYSEARCH_REPO_LOCATION>/video/<VIDEO_NAME>.h264" .```    
You can type ```video_*`` in VIDEO_NAME to pull all files from the repo (this may take more time)
4. To display it, the video must be converted to .mp4. Use this command (add location to VIDEO_NAME to put it elsewhere):
```ffmpeg -framerate 30 -i <VIDEO_NAME>.h264 -c copy -movflags +faststart <VIDEO_NAME>.mp4```  

(30 indicates framerate and was decided upon experimentally. A more accurate solution is developed).  
You can use this loop to convert all .h264 files that you pulled into .mp4 files and make them keep the same name.
```
for f in video_*.h264; do
  ffmpeg -y -fflags +genpts -framerate 30 -i "$f" -c copy -movflags +faststart "${f%.h264}.mp4"
done
```
