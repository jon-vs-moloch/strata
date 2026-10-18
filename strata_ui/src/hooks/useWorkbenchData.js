import { useState, useEffect, useRef } from 'react';
import axios from 'axios';

/**
 * Hook to fetch consolidated workbench detail for a task.
 * @param {string} taskId 
 * @param {string} apiBase 
 */
export const useWorkbenchData = (taskId, apiBase) => {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  
  // Simple cache to avoid re-fetching on history navigation if we just had it
  const cache = useRef({});

  useEffect(() => {
    if (!taskId || !apiBase) {
      setData(null);
      return;
    }

    if (cache.current[taskId]) {
      setData(cache.current[taskId]);
      // Still fetch in background to refresh? Let's stay simple for now:
      // return;
    }

    setLoading(true);
    axios.get(`${apiBase}/admin/tasks/${taskId}/detail`)
      .then(res => {
        if (res.data.status === 'ok') {
          cache.current[taskId] = res.data;
          setData(res.data);
          setError(null);
        } else {
          setError(res.data.message || 'Failed to fetch task detail');
        }
      })
      .catch(err => {
        console.error('Workbench data fetch failed:', err);
        setError(err.message || 'Network error fetching task detail');
      })
      .finally(() => {
        setLoading(true); // Wait, should be false
        setLoading(false);
      });
  }, [taskId, apiBase]);

  const refetch = () => {
    if (!taskId || !apiBase) return;
    setLoading(true);
    axios.get(`${apiBase}/admin/tasks/${taskId}/detail`)
      .then(res => {
        if (res.data.status === 'ok') {
          cache.current[taskId] = res.data;
          setData(res.data);
          setError(null);
        }
      })
      .finally(() => setLoading(false));
  };

  return { data, loading, error, refetch };
};
