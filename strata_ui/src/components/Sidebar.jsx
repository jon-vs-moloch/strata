import React from 'react';
import { LayoutDashboard, FileCode, Zap, Search, Settings } from 'lucide-react';
import Logo from './Logo';

const NavItem = ({ icon, label, active = false, onClick }) => {
  const IconComponent = icon;
  return (
  <div 
    className={`nav-item ${active ? 'active' : ''}`}
    onClick={onClick}
    style={{ display: 'flex', alignItems: 'center', gap: '12px', padding: '12px 16px', borderRadius: '8px', color: active ? '#8257e5' : '#9499ad', cursor: 'pointer', transition: 'all 0.2s ease', background: active ? 'rgba(130, 87, 229, 0.1)' : 'transparent', fontWeight: 600 }}
  >
    <IconComponent size={20} />
    <span>{label}</span>
  </div>
  );
};

const Sidebar = ({ activeTab, onTabChange }) => {
  return (
    <div className="sidebar" style={{ width: '280px', backgroundColor: '#141418', borderRight: '1px solid rgba(255, 255, 255, 0.1)', display: 'flex', flexDirection: 'column', padding: '32px 16px', gap: '48px' }}>
      <Logo />
      <div className="nav-list" style={{ listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <NavItem 
          icon={LayoutDashboard} 
          label="Tasks" 
          active={activeTab === 'tasks'} 
          onClick={() => onTabChange('tasks')}
        />
        <NavItem 
          icon={Zap} 
          label="Prompt Registry" 
          active={activeTab === 'prompts'} 
          onClick={() => onTabChange('prompts')}
        />
        <NavItem 
          icon={Search} 
          label="Research" 
          active={activeTab === 'research'} 
          onClick={() => onTabChange('research')}
        />
        <div style={{ marginTop: 'auto', paddingTop: '32px', borderTop: '1px solid rgba(255, 255, 255, 0.1)' }}>
          <NavItem icon={Settings} label="Settings" />
        </div>
      </div>
    </div>
  );
};

export default Sidebar;
