import {useEffect, useState} from 'react';
import {cancelRender, continueRender, delayRender, staticFile} from 'remotion';
import {BrainData, parseBrain} from '../scenes/brain/data';

let dataPromise: Promise<BrainData> | undefined;
const loadData = () => {
  if (!dataPromise) dataPromise = Promise.all(['fsaverage6_pial.bin', 'bbb_720p_55s/brain'].map(async (file) => {
    const response = await fetch(staticFile(file));
    if (!response.ok) throw new Error(`Brain asset ${file}: ${response.status}`);
    return response.arrayBuffer();
  })).then(([mesh, brain]) => parseBrain(mesh, brain));
  return dataPromise;
};

/** Real cortical mesh and predictions, shared by every walkthrough scene. */
export const useBrainData = (): BrainData | null => {
  const [hold] = useState(() => delayRender('Loading real cortical mesh and predictions'));
  const [data, setData] = useState<BrainData | null>(null);
  useEffect(() => {
    let mounted = true;
    loadData().then((loaded) => {
      if (mounted) {
        setData(loaded);
        continueRender(hold);
      }
    }).catch(cancelRender);
    return () => {
      mounted = false;
      continueRender(hold);
    };
  }, [hold]);
  return data;
};
