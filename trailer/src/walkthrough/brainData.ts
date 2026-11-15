import {useEffect, useState} from 'react';
import {cancelRender, continueRender, delayRender, staticFile} from 'remotion';
import {BrainData, parseBrain} from '../scenes/brain/data';
import {SWEE} from './timeline';

type Loaded = {data: BrainData | null; error: Error | null};
const cache = new Map<string, Promise<BrainData>>();

const load = (key: string, mesh: string, brain: string) => {
  let promise = cache.get(key);
  if (!promise) {
    promise = Promise.all([mesh, brain].map(async (file) => {
      const response = await fetch(staticFile(file));
      if (!response.ok) throw new Error(`Brain asset ${file}: ${response.status}`);
      return response.arrayBuffer();
    })).then(([meshBuffer, brainBuffer]) => parseBrain(meshBuffer, brainBuffer));
    cache.set(key, promise);
  }
  return promise;
};

const useBrainFile = (key: string, mesh: string, brain: string): Loaded => {
  const [hold] = useState(() => delayRender(`Loading ${key} brain`));
  const [loaded, setLoaded] = useState<Loaded>({data: null, error: null});
  useEffect(() => {
    let mounted = true;
    load(key, mesh, brain).then((data) => {
      if (mounted) {
        setLoaded({data, error: null});
        continueRender(hold);
      }
    }).catch((error: Error) => {
      if (mounted) {
        setLoaded({data: null, error});
        continueRender(hold);
      }
    });
    return () => {
      mounted = false;
      continueRender(hold);
    };
  }, [hold, key, mesh, brain]);
  return loaded;
};

/** Real cortical mesh and Swee Choon predictions. */
export const useSweeBrain = (): BrainData | null => {
  const {data, error} = useBrainFile('swee', 'fsaverage6_pial.bin', SWEE.brain);
  if (error) cancelRender(error);
  return data;
};
