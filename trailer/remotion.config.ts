import {Config} from '@remotion/cli/config';
import path from 'node:path';
if (process.platform === 'darwin' && process.arch === 'arm64') {
  Config.setBrowserExecutable(path.resolve('tools/chrome-local.sh'));
}
Config.setEntryPoint('./src/index.ts');
Config.setOutputLocation('out/neurolens_trailer.mp4');
Config.setCodec('h264');
Config.setCrf(17);
Config.setPixelFormat('yuv420p');
Config.setVideoImageFormat('jpeg');
Config.setJpegQuality(95);
Config.setConcurrency(4);
Config.setOverwriteOutput(true);
