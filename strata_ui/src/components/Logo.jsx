import React from 'react';
import { motion } from 'framer-motion';

const Logo = () => {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
      <motion.div 
        animate={{ rotate: 360 }}
        transition={{ duration: 20, repeat: Infinity, ease: "linear" }}
        style={{
          width: '32px',
          height: '32px',
          background: 'linear-gradient(135deg, #8257e5, #00d9ff)',
          borderRadius: '8px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: '0 0 15px rgba(130, 87, 229, 0.4)'
        }}
      >
        <div style={{ 
          width: '12px', 
          height: '12px', 
          background: 'white', 
          borderRadius: '2px',
          transform: 'rotate(45deg)' 
        }} />
      </motion.div>
      <span style={{ fontWeight: 800, fontSize: '20px', letterSpacing: '-0.5px' }}>
        SHOTGUN <span style={{ color: '#8257e5' }}>TOKENS</span>
      </span>
    </div>
  );
};

export default Logo;
